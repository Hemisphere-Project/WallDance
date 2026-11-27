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


# an engine file TensorRT could not deserialize (truncated, another TensorRT, not an engine): the operator reads
# this, the log keeps the raw error
ENGINE_UNREADABLE = "the engine file could not be read"
# ... but TensorRT gives no engine either when the GPU has no room for it (TouchDesigner grew beside the app,
# 'Error Code 2: OutOfMemory' in its log): the file is sound, and a rebuild -- minutes of GPU on show night --
# fixes nothing.  ``load_failure`` tells the two apart, and says "rebuild" only for a file shown bad.
NO_ROOM = "no room on the GPU"
NO_ENGINE = "TensorRT gave no engine"
# what a load of an engine takes on the GPU, for each byte of it: the engine itself and the memory it works in
# (mgr-ran's TensorRT logs, 1280 and 1920 x 1056: +143 to +468 MB beside 600 to 1200 MB of engine)
LOAD_NEEDS = 1.5
# what a failure is (``LoadFailure.kind``): the file shown bad / no room on the GPU / one of the two, not told /
# another error
FILE_BAD, ROOM, UNSURE, OTHER = "file", "room", "unsure", "other"


class LoadFailure(str):
    """``load_failure``'s answer: the reason in the operator's words -- it IS that string, so a caller that reads
    a string reads it as before -- with ``kind`` (FILE_BAD, ROOM, UNSURE, OTHER), what the checks found
    (``detail``, for the log) and the one thing to do (``todo``)."""
    kind: str
    detail: str

    def __new__(cls, why: str, kind: str = OTHER, detail: str = "") -> "LoadFailure":
        self = super().__new__(cls, why)
        self.kind, self.detail = kind, detail
        return self

    def todo(self, cand: Candidate, *, apply: bool = False) -> str:
        """What to do: for the automatic swipe (its ban holds until Dismiss), or for the operator's Apply."""
        rebuild = f"rebuild {cand.engine_file} (extra/build_engines)"
        if self.kind == ROOM:
            return ("close what else uses the GPU before trying it again" if apply else
                    "close what else uses the GPU, then Dismiss lets it try again, or set the automatic swipe to off")
        if self.kind == UNSURE:                  # never a rebuild first: the GPU's room is the quick thing to try
            return ("close what else uses the GPU before trying it again" if apply else
                    "close what else uses the GPU, then Dismiss lets it try again") + f"; if it fails again, {rebuild}"
        if apply:
            return _rebuild(cand)
        return (f"{rebuild}, then Dismiss lets it try again" if self.kind == FILE_BAD else
                "Dismiss lets it try again, or set the automatic swipe to off")


def gpu_free_mb() -> Optional[float]:
    """The GPU's free memory now, in MB (what TouchDesigner and the rest leave), or None when it cannot be read
    (CUDA not started in this process: a CPU test)."""
    try:
        import torch
        if not torch.cuda.is_initialized():
            return None
        return torch.cuda.mem_get_info()[0] / 2 ** 20
    except Exception:  # noqa: BLE001
        return None


def _tensorrt_here() -> Optional[Tuple[int, int]]:
    """The TensorRT this process runs (major, minor), None before anything imported it."""
    import sys
    try:
        major, minor = str(sys.modules["tensorrt"].__version__).split(".")[:2]
        return int(major), int(minor)
    except Exception:  # noqa: BLE001
        return None


def engine_file_fault(path: str) -> Tuple[Optional[str], int]:
    """What shows an engine file bad, read without TensorRT (its size, its first bytes): (the fault, None when the
    file looks whole; the size of TensorRT's part, in bytes).  The file ultralytics writes: a 4-byte length and a
    JSON metadata block, then TensorRT's own: b'ftrt', its size at bytes 16-23, the TensorRT that built it at bytes
    24 and 25 (major, minor) -- so on every engine of mgr-ran, 27/11 (11.3 and the June 11.0 ones).  Damaged bytes
    inside a whole file are not seen: such a file looks whole."""
    import json
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            n, start = int.from_bytes(f.read(4), "little"), 0
            if 0 < n <= size - 4:                # ultralytics' header, as its AutoBackend reads it
                try:
                    json.loads(f.read(n))
                    start = 4 + n
                except ValueError:
                    pass
            f.seek(start)
            head = f.read(32)
    except OSError as e:
        return f"cannot be opened: {e.strerror or e}", 0
    plan = size - start
    if len(head) < 32 or head[:4] != b"ftrt":
        return "not a TensorRT engine", plan
    declared = int.from_bytes(head[16:24], "little")
    if declared > plan:
        return f"cut short: {plan} of its {declared} bytes", plan
    here = _tensorrt_here()
    if here is not None and (head[24], head[25]) != here:
        return f"built by TensorRT {head[24]}.{head[25]}, this machine runs {here[0]}.{here[1]}", plan
    return None, plan


def no_room(free_mb: Optional[float], need_mb: Optional[float] = None, detail: str = "") -> LoadFailure:
    """The GPU had no room: with the free MB when they can be read, and what a load takes when it is known."""
    why = NO_ROOM if free_mb is None else f"{NO_ROOM}: {free_mb:.0f} MB free"
    if free_mb is not None and need_mb is not None:
        why += f", a load of it takes about {max(100.0, round(need_mb, -2)):.0f} MB"
    return LoadFailure(why, ROOM, detail)


# the engine was read but TensorRT gave no execution context (the memory it works in): ultralytics then fails on
# ``None.set_input_shape`` (or the other calls below) -- the file is sound, the GPU had no room
_NO_CONTEXT = ("set_input_shape", "get_tensor_shape", "set_binding_shape", "get_binding_shape")


def out_of_memory(text: str) -> bool:
    """An error that says the GPU ran out of memory (PyTorch's, CUDA's, TensorRT's own words)."""
    t = text.lower()
    return "out of memory" in t or "outofmemory" in t


def load_failure(exc: BaseException, path: Optional[str] = None, *,
                 free_mb: Optional[float] = None) -> LoadFailure:
    """Why an engine did not load, in the operator's words (a ``LoadFailure``: the reason, its kind, what to do).

    TensorRT gives no engine -- ultralytics then fails on ``None.create_execution_context`` -- or raises a
    serialization error for a bad file AND for a GPU without room for it.  With the engine's ``path`` the two are
    told apart: a file shown bad (``engine_file_fault``) is ``ENGINE_UNREADABLE`` (rebuild it); a file that looks
    whole on a GPU with less free memory than a load of it takes (``LOAD_NEEDS``) is ``NO_ROOM``, with the free
    MB; a file that looks whole with that room free now (the memory a failed load took is back: something else
    may have held it) names both, a rebuild only if it fails again.  An out-of-memory error, or no execution
    context (the engine was read), is NO_ROOM.  Anything else, the error itself.  ``free_mb``: the reading (else
    ``gpu_free_mb()``, now).  Without ``path`` (the old call) nothing is read: no engine is ``ENGINE_UNREADABLE``,
    the rest the error itself, as before."""
    text = str(exc)
    raw = f"{type(exc).__name__}: {text[:100]}"
    no_engine = (isinstance(exc, AttributeError) and "create_execution_context" in text) or "serializ" in text.lower()
    if path is None:
        return LoadFailure(ENGINE_UNREADABLE, FILE_BAD) if no_engine else LoadFailure(raw)
    oom = out_of_memory(f"{type(exc).__name__}: {text}") or (
        isinstance(exc, AttributeError) and "'NoneType'" in text and any(a in text for a in _NO_CONTEXT))
    if not no_engine and not oom:
        return LoadFailure(raw)
    free = gpu_free_mb() if free_mb is None else free_mb
    seen = "free GPU memory not known" if free is None else f"{free:.0f} MB free on the GPU"
    fault, plan = engine_file_fault(path)
    need = None if fault is not None else plan * LOAD_NEEDS / 2 ** 20
    if oom:
        return no_room(free, need, f" ({seen})")
    if fault is not None:
        return LoadFailure(ENGINE_UNREADABLE, FILE_BAD, f" (the file: {fault})")
    detail = f" (the file looks whole: {plan / 2 ** 20:.0f} MB of engine, a load takes about {need:.0f} MB; {seen})"
    if free is not None and free < need:
        return no_room(free, need, detail)
    return LoadFailure(f"{NO_ENGINE}: too little room on the GPU at that moment, or a damaged file" if free is None
                       else f"{NO_ENGINE}, {free:.0f} MB free on the GPU now: too little room at that moment, or a "
                            f"damaged file", UNSURE, detail)


def run_failure(error: str, *, free_mb: Optional[float] = None) -> LoadFailure:
    """Why a loaded engine's inference failed (``CandidateRun.error``: "inference failed: <type>: <message>"), in
    the operator's words: out of memory is NO_ROOM, as for a load; anything else, what failed and the error's type
    (its message is for the log)."""
    if out_of_memory(error):
        return no_room(gpu_free_mb() if free_mb is None else free_mb)
    parts = error.split(": ")
    return LoadFailure(": ".join(parts[:2]) if len(parts) > 1 else error[:100])


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
        # live model (the automatic swipe: as its resident); (request) once its first frames ran well; (request)
        # on any switch that did not happen; (request) when a model load replaced it during its first frames
        self.on_switched: Optional[Callable[..., bool]] = None
        self.on_confirmed: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_failed: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_superseded: Optional[Callable[[Dict[str, Any]], None]] = None
        # (request) -> why a switch asked earlier must not go on now, or None: asked before the load, before the
        # warm-up and right before the swap (the app: the automatic swipe turned off meanwhile) -- abandoned then
        self.why_not: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None
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
            # right after the loop's next live pass (a TensorRT destructor holds the GIL some 50 ms): a pacer made
            # now waits for one -- at most 0.2 s, none in STANDBY or with the loop stopped
            make_pacer(proc, 0.0, self._stop, start=time.perf_counter())()
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
        held = self._held(want)
        if held:
            self._abandon(want, f"not switched to {winner.label}: {held}")
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
                why = load_failure(exc)
                if why == ENGINE_UNREADABLE:            # the raw error for the log, plain words for the operator
                    say(f"[Switch] {winner.engine_file} did not load: {type(exc).__name__}: {exc}")
                self._failed_before(want, live, why)
                return
        held = self._held(want)                         # changed during the load
        if held:
            if loaded_here:
                self.release((prep.model, prep.runner))
            self._abandon(want, f"not switched to {winner.label}: {held}")
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
        """On the loop thread, between two frames: the prepared switch, if any.  True when one was handled.
        First, a probation that a model load overtook ends here (``_superseded``)."""
        if self._probation is not None:
            self._superseded()
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
            held = self._held(req)
            if held:
                if req["loaded_here"]:
                    self.release((prep.model, prep.runner))
                self._abandon(req, f"not switched to {req['winner'].label}: {held}")
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

    def _superseded(self) -> None:
        """On the loop thread: is the model on probation still the processor's?  A model load (the operator changed
        the model or the input, a STANDBY Apply, a project load) may have replaced it before its first frames ran:
        neither ``on_ok`` nor ``on_fail`` comes then, and without this the switch stayed busy for good.  The
        probation ends as superseded: not a fault, no ban, no rollback (the model loaded is the live one, the
        operator chose it); the switched-in model joins the models to free (the old live one is there already,
        or kept by the caller), the processor forgets it -- in STANDBY no frame comes to do it."""
        req = self._probation
        prep = req["prepared"]
        proc = self.processor()
        if getattr(proc, "model", None) is prep.model:
            return                             # still live: its first frames decide
        forget = getattr(proc, "end_probation", None)
        if forget is not None:
            forget(prep.model)
        self.release((prep.model, prep.runner))
        self._probation = None
        self.state = "idle"
        if self.last_switch is not None:
            self.last_switch["superseded"] = True
        live = self.live()
        text = f"the switch to {req['new'].label} was overtaken by a model load during its first frames" + \
               (f": {live.candidate.label} runs" if live is not None else "")
        self._record("superseded", text=text)
        say(f"[Switch] {text}")
        if self.on_superseded is not None:
            try:
                self.on_superseded(req)
            except Exception as exc:  # noqa: BLE001
                say(f"[Switch] superseded handler failed: {type(exc).__name__}: {exc}")
        self.notify("cleared", {"text": text})
        self._done(req, False, text)

    def _swap_ok(self, req: Dict[str, Any]) -> None:
        self._probation = None
        self.state = "idle"
        self.fault = None                       # a later switch succeeded
        if self.last_switch is not None:
            self.last_switch["confirmed"] = True
        if self.on_confirmed is not None:
            try:
                self.on_confirmed(req)
            except Exception as exc:  # noqa: BLE001
                say(f"[Switch] confirmation handler failed: {type(exc).__name__}: {exc}")
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
    def _held(self, req: Dict[str, Any]) -> Optional[str]:
        """``why_not(req)``, None without it; when it raises, held (no switch: the live model runs on)."""
        if self.why_not is None:
            return None
        try:
            return self.why_not(req)
        except Exception as exc:  # noqa: BLE001
            say(f"[Switch] hold check failed: {type(exc).__name__}: {exc}")
            return f"its check failed ({type(exc).__name__})"

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
