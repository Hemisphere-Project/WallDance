"""The seamless model switch (Plan Cordee P6): another model loaded beside the live one and swapped in between two
frames -- no camera close, no modal, no pause, so usable during a show.  The operator's Apply takes it whenever the
winner's TensorRT engine is built (and the live model runs TensorRT); the automatic swipe switches through it too.

* ``request(winner, text, on_done)``: on the loop thread (a command).  This module's worker thread then loads the
  winner's engine beside the live one -- under ``ENGINE_LOAD_LOCK`` and without a CUDA graph (a capture beside the
  live loop is what ``cudaErrorStreamCaptureImplicit`` was made of) -- checks that it runs TensorRT, and calls it
  once with the arguments the live call will take at the winner's input (PoseRunner's key then matches: the first
  live frame after the switch takes the fast path).  A model already loaded by the caller (the automatic swipe's
  resident) skips the load: ``prepared``.
* ``drain()``: on the loop thread, between two frames (``MainLoop._tick_pumps``).  The port (``ModelController
  .swap_live``) swaps model, runner, network input size and shape together; after a size change the tracker keeps
  its space (``FrameProcessor.adopt_model``: the new network's detections are converted into it), so no track
  moves -- reached only with ``size_change`` (off: gate a, ``SIZE_CHANGE_SEAMLESS``; another input size is the
  STANDBY reload).  Never while someone else is inferring on a prepared model (``guard``).
* probation: the switched-in model's first frames; if one raises, the old model (and input) takes that very frame
  back, the bookkeeping follows (``port.restore_live``) and the failure is the ``fault``.
* the fault: a switch that did not happen -- the engine did not load, would run PyTorch, raised -- is the one
  swipe fault of the health line (``health()``, plain ASCII: what runs, the one thing to do), cleared by the
  operator's acknowledgement (``dismiss``) or a later switch that succeeds.  A switch that did not happen never
  leaves the new engine live.
* models that leave (the old live one, a failed one) are freed on the worker thread, right after a live frame of
  the current model, under the engine lock (their destructors free the engines).

Threading: the worker owns loading, warming and freeing; the loop thread owns the live model and the swap.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from core.swipe import Candidate
from core.swipe_runner import CandidateRunner, SwipeInput, install_capture_guards, no_graph_capture
from runtime.swipe_controller import LiveSetting, make_pacer, say


# Gate (a), 26/11 on mgr-ran: switched to another input size between two frames (the tracker pinned to the old
# input's space), a dancer's emitted point stood still for about four frames, then stepped further than the take's
# own frame-to-frame p99 (mur2 slot 8, a moving dancer: 46.6 % of her height, the take's p99 34.4 %; the run without
# a switch did not).  Another input size is therefore the STANDBY reload; only the same-input switch is seamless.
# The pin and the conversion stay (tested) for the next gate: ``SeamlessSwitch(size_change=True)``.
SIZE_CHANGE_SEAMLESS = False


def net_input(c: Candidate) -> Tuple[int, Optional[Tuple[int, int]]]:
    """A setting's network input: (imgsz, rectangle or None)."""
    return int(c.imgsz), (tuple(c.shape) if c.shape else None)


@dataclass
class Prepared:
    """A model loaded beside the live one, with its PoseRunner."""
    candidate: Candidate
    model: Any
    runner: Any
    load_s: float = 0.0


def runs_tensorrt(model) -> Optional[bool]:
    """Whether a loaded ultralytics model runs a TensorRT engine (its AutoBackend's format), None when it cannot be
    told (a test's fake)."""
    be = getattr(getattr(model, "predictor", None), "model", None)
    if be is None:
        return None
    fmt = getattr(be, "format", None)
    if isinstance(fmt, str):
        return fmt == "engine"
    eng = getattr(be, "engine", None)
    return bool(eng) if isinstance(eng, bool) else None


def _runner_factory(proc) -> CandidateRunner:
    return CandidateRunner(SwipeInput.from_processor(proc), bg_subtractor=getattr(proc, "bg_subtractor", None))


def _rebuild(cand: Candidate) -> str:
    return f"rebuild {cand.engine_file} (extra/build_engines) before trying it again"


class SeamlessSwitch:
    def __init__(self, *, processor: Callable[[], Any], live: Callable[[], Optional[LiveSetting]],
                 engine_path: Callable[[Candidate], str], port,
                 notify: Callable[[str, Dict[str, Any]], None] = lambda kind, payload: None,
                 runner_factory: Callable[[Any], Any] = _runner_factory,
                 engine_exists: Callable[[str], bool] = os.path.exists, engine_lock=None,
                 period_s: float = 1.0, probation_frames: int = 3, size_change: bool = SIZE_CHANGE_SEAMLESS):
        self.processor = processor
        self.size_change = bool(size_change)     # another input size switched between two frames (gate a: off)
        self.live = live
        self.engine_path = engine_path
        self.port = port                 # swap_live(model, runner, cand, on_fail, on_ok) -> old pair; restore_live(old)
        self.notify = notify
        self.runner_factory = runner_factory
        self.engine_exists = engine_exists
        if engine_lock is None:
            try:
                from core.model_manager import ENGINE_LOAD_LOCK as engine_lock
            except Exception:  # noqa: BLE001 - no ultralytics (CPU tests)
                engine_lock = threading.RLock()
        self.engine_lock = engine_lock
        self.period_s = float(period_s)
        self.probation_frames = int(probation_frames)
        # held while a prepared model is warmed here or inferred on elsewhere (the automatic swipe's resident):
        # the swap waits for the next frame rather than take a model that is busy on another thread
        self.guard = threading.Lock()
        self._ask_lock = threading.Lock()         # one request at a time (the operator's, the automatic swipe's)
        self.state = "idle"                       # idle | loading | switching
        self.fault: Optional[str] = None
        self.last_switch: Optional[Dict[str, Any]] = None
        self.history: Deque[Dict[str, Any]] = deque(maxlen=200)
        # (request, old live pair, old candidate, new candidate, take time) -> True when the caller keeps the old
        # live model (the automatic swipe: as its resident); (request) on any switch that did not happen
        self.on_switched: Optional[Callable[..., bool]] = None
        self.on_failed: Optional[Callable[[Dict[str, Any]], None]] = None
        self._want: Optional[Dict[str, Any]] = None       # asked, for the worker
        self._request: Optional[Dict[str, Any]] = None    # prepared, for the loop
        self._probation: Optional[Dict[str, Any]] = None  # swapped in, its first frames not yet run
        self._drop: List[Tuple[Any, Any]] = []            # models to free on the worker, after a live frame
        self._model_seen: Optional[Tuple[int, float]] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ the worker thread
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="SeamlessSwitch", daemon=True)
        self._thread.start()

    def stop(self, timeout: Optional[float] = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None:
            t.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.period_s)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.service()
            except Exception as exc:  # noqa: BLE001 - never the app
                say(f"[Switch] {type(exc).__name__}: {exc}")

    def service(self) -> None:
        """One pass of the worker: free what waits, prepare what is asked."""
        self.free_pending()
        if self._want is not None:
            self._prepare()

    def _record(self, kind: str, **data) -> Dict[str, Any]:
        row = {"kind": kind, "wall": time.time(), **data}
        self.history.append(row)
        return row

    def ran_a_frame(self, proc) -> bool:
        """The current live model has run a frame since this thread first saw it (its first RUN frame -- which
        may capture a CUDA graph on mgr-ran's ultralytics -- is behind us): only then is a model freed."""
        try:
            if proc._swipe_key is not None and proc._swipe_key == proc._swipe_setting_key():
                return True                    # the swipe's tap saw a frame of the current setting
        except Exception:  # noqa: BLE001
            pass
        mid, t = id(getattr(proc, "model", None)), float(getattr(proc, "frame_done_t", 0.0) or 0.0)
        seen = self._model_seen
        if seen is None or seen[0] != mid:
            self._model_seen = (mid, t)
            return False
        return t > seen[1]

    def release(self, pair: Tuple[Any, Any]) -> None:
        """A model for the worker to free (refcount), when it is safe."""
        self._drop.append(pair)

    def free_pending(self, extra: Optional[Callable[[], None]] = None) -> bool:
        """Free the waiting models (and run ``extra`` -- the automatic swipe's resident release -- in the same
        safe window): right after a live frame of the current model, under the engine lock, never while a
        switch is on its way or on probation.  True when the window was open."""
        if not self._drop and extra is None:
            return False
        proc = self.processor()
        if self.live() is None or self._request is not None or self._probation is not None \
                or not self.ran_a_frame(proc):
            return False
        with self.engine_lock:
            make_pacer(proc, 0.0, self._stop)()            # right after a live frame
            if extra is not None:
                extra()
            n = len(self._drop)
            self._drop.clear()                              # refcount frees the engines here
            if n:
                self._record("free", models=n)
        return True

    def warm(self, prepared: Prepared, target: Candidate) -> Optional[str]:
        """``prepared`` called once with the arguments the live call takes at ``target``'s input; also the last
        check that it runs at all and runs TensorRT.  None when ready, else why not."""
        try:
            import torch
            kw = self.processor().live_infer_args()
            kw["imgsz"] = CandidateRunner.imgsz_arg(target)
            imgsz = kw["imgsz"]
            th, tw = imgsz if isinstance(imgsz, tuple) else (imgsz, imgsz)
            with self.engine_lock, self.guard, no_graph_capture():
                z = torch.full((1, 3, th, tw), 0.5, device="cuda" if torch.cuda.is_available() else "cpu")
                prepared.runner(z, **kw)
            if runs_tensorrt(prepared.model) is False:
                return "it would run PyTorch, not its TensorRT engine"
            return None
        except Exception as exc:  # noqa: BLE001
            return f"{type(exc).__name__}: {str(exc)[:120]}"

    def _prepare(self) -> None:
        want, self._want = self._want, None
        winner = want["winner"]
        live = self.live()
        if live is None or live.key != want["live_key"]:
            self._abandon(want, f"not switched to {winner.label}: the setting changed meanwhile")
            return
        prep, loaded_here = want.get("prepared"), False
        if prep is None:
            self.state = "loading"
            try:
                install_capture_guards()
                runner = self.runner_factory(self.processor())
                with self.engine_lock, no_graph_capture():
                    model, pose_runner, load_s = runner.load(self.engine_path(winner), winner)
                prep, loaded_here = Prepared(winner, model, pose_runner, load_s), True
                self._record("load", model=winner.label, seconds=round(load_s, 2))
                say(f"[Switch] {winner.label} loaded beside the live model ({load_s:.2f} s)")
            except Exception as exc:  # noqa: BLE001 - the engine never becomes live
                self._failed_before(want, live, f"{type(exc).__name__}: {str(exc)[:100]}")
                return
        err = self.warm(prep, winner)
        if err:
            if loaded_here:
                self.release((prep.model, prep.runner))
            self._failed_before(want, live, err)
            return
        self._request = {**want, "prepared": prep, "loaded_here": loaded_here, "from": live.candidate}
        self.state = "switching"

    # ------------------------------------------------------------------ asking
    def busy(self) -> Optional[str]:
        """Why another switch cannot be asked now (None: it can)."""
        if self._want is not None or self._request is not None or self._probation is not None:
            return "a switch is already under way"
        return None

    def can_switch(self, winner: Candidate) -> Optional[str]:
        """None when ``winner`` can be switched to seamlessly (its engine built, the live model on TensorRT),
        else why not (the Apply is then the STANDBY reload)."""
        live = self.live()
        if live is None:
            return "a model load is under way"
        if not live.trt:
            return "the live model runs PyTorch"
        if not self.engine_exists(self.engine_path(winner)):
            return f"its engine is not built on this machine ({winner.engine_file})"
        if not self.size_change and net_input(winner) != net_input(live.candidate):
            return "another input size"
        return None

    def request(self, winner: Candidate, text: str, on_done: Optional[Callable[[bool, str], None]] = None, *,
                prepared: Optional[Prepared] = None, auto: bool = False) -> Optional[str]:
        """Ask for the switch (the operator's Apply on the loop thread, the automatic swipe on its own; nothing
        here waits).  ``on_done(ok, text)`` runs on the loop thread once the switched-in model has run its first
        frames, or once the switch failed or was abandoned.  None when under way, else why not."""
        with self._ask_lock:
            live = self.live()
            why = (self.can_switch(winner) if prepared is None else
                   ("a model load is under way" if live is None else None)) or self.busy()
            if why:
                return why
            self._want = {"winner": winner, "text": text, "live_key": live.key, "on_done": on_done,
                          "prepared": prepared, "auto": bool(auto)}
            self.state = "loading"
        self.start()
        self._wake.set()
        return None

    # ------------------------------------------------------------------ the switch (loop thread)
    def drain(self) -> bool:
        """On the loop thread, between two frames: the prepared switch, if any.  True when one was handled."""
        req = self._request
        if req is None:
            return False
        if not self.guard.acquire(blocking=False):
            return False                       # the prepared model is busy on another thread: the next frame
        try:
            self._request = None
            live = self.live()
            prep = req["prepared"]
            if live is None or live.key != req["live_key"]:
                if req["loaded_here"]:
                    self.release((prep.model, prep.runner))
                self._abandon(req, f"not switched to {req['winner'].label}: the setting changed meanwhile")
                return True
            old_cand, new_cand = live.candidate, prep.candidate
            take_t = self.processor().loop_clock()

            def failed(exc: BaseException, failed_pair=None) -> None:
                self._swap_failed(req, exc, failed_pair)

            def ok() -> None:
                self._swap_ok(req)
            try:
                old_pair = tuple(self.port.swap_live(prep.model, prep.runner, new_cand, on_fail=failed, on_ok=ok))
            except Exception as exc:  # noqa: BLE001 - the port put the old model back
                if req["loaded_here"]:
                    self.release((prep.model, prep.runner))
                self._fail(req, f"{type(exc).__name__}: {str(exc)[:100]}", old_cand, new_cand, before=False)
                return True
            keep = False
            if self.on_switched is not None:
                try:
                    keep = bool(self.on_switched(req, old_pair, old_cand, new_cand, take_t))
                except Exception as exc:  # noqa: BLE001
                    say(f"[Switch] switch handler failed: {type(exc).__name__}: {exc}")
            if not keep:
                self.release(old_pair)
            req.update(old_pair=old_pair, old_kept=keep, old=old_cand, new=new_cand, take_t=take_t)
            self._probation = req
            self.last_switch = self._record("switch", old=old_cand.label, new=new_cand.label, t=take_t,
                                            auto=req["auto"], text=req["text"], confirmed=False,
                                            size_change=(int(old_cand.imgsz), old_cand.shape)
                                            != (int(new_cand.imgsz), new_cand.shape))
            say(f"[Switch] {old_cand.label} -> {new_cand.label} at {take_t:.1f} s ({req['text']})")
            return True
        finally:
            self.guard.release()

    def _swap_ok(self, req: Dict[str, Any]) -> None:
        self._probation = None
        self.state = "idle"
        self.fault = None                       # a later switch succeeded
        if self.last_switch is not None:
            self.last_switch["confirmed"] = True
        new = req["new"]
        text = f"switched to {new.label} at {time.strftime('%H:%M')}"
        self.notify("switched", {"text": text, "to": new.label, "from": req["old"].label, "auto": req["auto"]})
        self._done(req, True, text)

    def _swap_failed(self, req: Dict[str, Any], exc: BaseException, failed_pair=None) -> None:
        """On the loop thread, from the processor: the old model (and input) already took the frame back.  The old
        live model -- queued to be freed, or kept by the caller -- is live again: it leaves both."""
        self._probation = None
        old_pair = req.get("old_pair")
        try:
            self.port.restore_live(req["old"])
        except Exception as e:  # noqa: BLE001
            say(f"[Switch] restoring the live model's bookkeeping failed: {e}")
        if old_pair is not None:
            self._drop = [d for d in self._drop if d[0] is not old_pair[0]]
        if failed_pair is not None:
            self.release(tuple(failed_pair))
        self._fail(req, f"{type(exc).__name__}: {str(exc)[:100]}", req["old"], req["new"], before=False)

    # ------------------------------------------------------------------ outcomes
    def _done(self, req: Dict[str, Any], ok: bool, text: str) -> None:
        cb = req.get("on_done")
        if cb is not None:
            try:
                cb(ok, text)
            except Exception as exc:  # noqa: BLE001
                say(f"[Switch] completion handler failed: {type(exc).__name__}: {exc}")

    def _abandon(self, req: Dict[str, Any], text: str) -> None:
        self.state = "idle"
        self._record("abandoned", text=text)
        self.notify("cleared", {"text": text})
        self._done(req, False, text)

    def _failed_before(self, want: Dict[str, Any], live: LiveSetting, why: str) -> None:
        self._fail(want, why, live.candidate, want["winner"], before=True)

    def _fail(self, req: Dict[str, Any], why: str, old: Candidate, new: Candidate, *, before: bool) -> None:
        what = "the automatic switch" if req.get("auto") else "the switch"
        # an automatic switch that failed is not tried again for that setting until Dismiss (the automatic swipe
        # keeps the ban: ``on_failed``); suggestions go on
        todo = (f"the automatic swipe will not try {new.engine_file} again until you press Dismiss"
                if req.get("auto") else _rebuild(new))
        text = f"{what} to {new.label} failed{' before it began' if before else ''} ({why}): {old.label} still " \
               f"runs - {todo}"
        self.fail(text)
        if self.on_failed is not None:
            try:
                self.on_failed(req)
            except Exception as e:  # noqa: BLE001
                say(f"[Switch] failure handler failed: {e}")
        self._done(req, False, text)

    def fail(self, text: str) -> None:
        """THE swipe fault (plain ASCII: what failed, what runs, the one thing to do)."""
        self.fault = text
        self.state = "idle"
        self._record("fault", text=text)
        say(f"[Switch] {text}")
        self.notify("fault", {"text": text})

    def dismiss(self) -> None:
        """The operator's acknowledgement: the fault goes."""
        if self.fault is not None:
            self.fault = None
            self.notify("cleared", {"text": "dismissed"})

    def health(self) -> Optional[str]:
        """The fault for the health line (a switch that failed), or None.  No GUI import needed."""
        return self.fault

    def status(self) -> Dict[str, Any]:
        return {"state": self.state, "fault": self.fault, "last_switch": self.last_switch,
                "waiting_to_free": len(self._drop)}
