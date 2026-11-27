"""P6, the second re-read (b1 finding 1, b2 finding 6): what the app tells the technician when an engine fails.

* The operator's seamless Apply warms the engine it loaded (``SeamlessSwitch.warm``).  A warm-up that ran out of
  GPU memory said « rebuild <file> (extra/build_engines) » -- minutes of GPU on show night, and it fixes nothing --
  and so did any other warm-up error.  Now the warm-up reads the same answer as the load: no room on the GPU (with
  the free MB) for an out-of-memory error, its first frame failed for any other; "rebuild" only for a file shown
  bad.
* No Python class name in a sentence the technician reads (the alert, the red line, the readiness row, /status):
  a few plain words and one thing to do; the error's type and message go to the log line.
* The ban names the setting (``l @ 1280``), never its engine file.
"""
import re

import pytest

from test_seamless_switch import L1280, L1920, _Loader, _Port, _live, _sw
from test_swipe_auto_failures import _broken_bg, _faults

OOM = "CUDA error: out of memory"
ILLEGAL = "CUDA error: an illegal memory access was encountered"
# a Python class in a sentence: its name (RuntimeError, OutOfMemoryError, SomeException) or a dotted class path
CLASSY = re.compile(r"Error|Exception|\b[a-z_]\w*(?:\.[a-z_]\w*)*\.[A-Z]\w*")


@pytest.fixture
def _free(monkeypatch):
    """The GPU's free memory, as the switch reads it after a failure (a CPU test has no CUDA)."""
    import runtime.seamless_switch as ss
    monkeypatch.setattr(ss, "gpu_free_mb", lambda: 312.4, raising=False)


class _WarmRaises(_Loader):
    """The engine loads; its first call -- the Apply's warm-up -- raises ``exc``."""

    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    def load(self, path, cand, model=None):
        m, _, load_s = super().load(path, cand, model)

        def runner(tensor, **kw):
            raise self.exc
        return m, runner, load_s


class _RefusingPort(_Port):
    """The processor refuses the swap itself (the port put the old model back)."""

    def swap_live(self, model, runner, cand, on_fail=None, on_ok=None):
        raise RuntimeError("the processor refused the swap (injected)")


def _apply(sw, winner=L1920, auto=False):
    done = []
    assert sw.request(winner, "Apply", on_done=lambda ok, text: done.append((ok, text)), auto=auto) is None
    sw.service()
    return done


def _plain(texts):
    """Every sentence the technician reads holds no Python class."""
    bad = [t for t in texts if t and CLASSY.search(t)]
    assert not bad, f"a Python class reached the operator: {bad}"


# ------------------------------------------------------------------------- 1. the Apply's warm-up (b1 finding 1)
def test_an_apply_whose_warm_up_runs_out_of_gpu_memory_says_no_room_never_rebuild(_free, capsys):
    """The reviewer's probe E: TouchDesigner grew since the load, the warm-up raises CUDA out of memory.  It read
    « ... (RuntimeError: CUDA error: out of memory): x @ 1280 still runs - rebuild yolo11l-pose_1280.engine
    (extra/build_engines) before trying it again »."""
    live, port = {"v": _live()}, _Port()
    sw, _, events = _sw(live, loader=_WarmRaises(RuntimeError(OOM)), port=port)
    done = _apply(sw)
    assert "rebuild" not in sw.fault, f"an out-of-memory warm-up sends the technician to rebuild: {sw.fault!r}"
    text = ("the switch to l @ 1920 x 1056 failed before it began (no room on the GPU: 312 MB free): x @ 1280 still "
            "runs - close what else uses the GPU before trying it again")
    assert sw.fault == text and sw.health() == text and done == [(False, text)] and _faults(events) == [text]
    assert port.calls == [] and sw.busy() is None                       # the live model never stopped
    assert f"RuntimeError: {OOM}" in capsys.readouterr().out              # the raw error: the log


def test_an_apply_whose_warm_up_raises_another_error_never_says_rebuild(capsys):
    """Any other warm-up error: the file loaded, nothing shows it bad -- no rebuild, one plain thing to do."""
    live, port = {"v": _live()}, _Port()
    sw, _, _ = _sw(live, loader=_WarmRaises(ValueError("shape mismatch (injected)")), port=port)
    done = _apply(sw)
    assert "rebuild" not in sw.fault, f"an unknown warm-up error sends the technician to rebuild: {sw.fault!r}"
    text = ("the switch to l @ 1920 x 1056 failed before it began (its first frame failed): x @ 1280 still runs - "
            "keep it for now")
    assert sw.fault == text and done == [(False, text)] and port.calls == []
    assert "ValueError: shape mismatch (injected)" in capsys.readouterr().out


def test_an_apply_whose_load_fails_on_a_file_shown_bad_still_says_rebuild(_free, tmp_path):
    """Regression guard (as today, passes before the change too): the file itself shown bad is the one case told
    to rebuild it, whatever the GPU's room."""
    bad = tmp_path / "yolo11l-pose_1920x1056.engine"
    bad.write_bytes(b"not a TensorRT engine\n")
    none = AttributeError("'NoneType' object has no attribute 'create_execution_context'")

    class _NoEngine(_Loader):
        def load(self, path, cand, model=None):
            self.loads.append(path)
            raise none
    live, port = {"v": _live()}, _Port()
    sw, _, _ = _sw(live, loader=_NoEngine(), port=port)
    sw.engine_path = lambda c: str(bad)
    _apply(sw)
    assert sw.fault == ("the switch to l @ 1920 x 1056 failed before it began (the engine file could not be read): "
                        f"x @ 1280 still runs - rebuild {L1920.engine_file} (extra/build_engines) before trying it "
                        "again")


# ------------------------------------------------------------- 2. no class name reaches the operator (b2 finding 6)
def _switch_path(how):
    """The switch's failure paths: (the switch, what the operator was told -- the fault, the toasts, on_done)."""
    live = {"v": _live()}
    port = _RefusingPort() if how == "the swap refused" else _Port()
    loader = {"a load error": _Loader(fail=ILLEGAL), "a warm-up error": _WarmRaises(ValueError("bad shape")),
              "a warm-up out of memory": _WarmRaises(RuntimeError(OOM))}.get(how, _Loader())
    sw, _, events = _sw(live, loader=loader, port=port)
    if how == "its hold check raised":
        def raises(req):
            raise KeyError("live_key")
        sw.why_not = raises
    done = _apply(sw, auto=how == "an automatic switch's first live frames")
    if how == "the swap refused":
        assert sw.drain()                                # the swap, between two frames: refused
    if how in ("its first live frames", "an automatic switch's first live frames"):
        assert sw.drain()
        port.hooks[0](RuntimeError("the engine raised on its first frame"), None)
    told = [sw.fault, sw.health(), str(sw.status())] + [p.get("text") for _, p in events] + [t for _, t in done]
    return sw, told


@pytest.mark.parametrize("how, error", [
    ("a load error", "RuntimeError"),
    ("a warm-up error", "ValueError"),
    ("a warm-up out of memory", "RuntimeError"),
    ("the swap refused", "RuntimeError"),
    ("its first live frames", "RuntimeError"),
    ("an automatic switch's first live frames", "RuntimeError"),
    ("its hold check raised", "KeyError"),
])
def test_no_class_name_reaches_the_operator_from_the_switch(how, error, _free, capsys):
    sw, told = _switch_path(how)
    assert any(told), f"{how}: nothing was said"
    _plain(told)
    assert f"{error}: " in capsys.readouterr().out, f"{how}: the log line lost the error's type"


@pytest.mark.parametrize("how", ["its load", "its run"])
def test_no_class_name_reaches_the_operator_from_the_automatic_swipe(how, capsys):
    """The automatic swipe's second engine: the fault (the alert, the red line), the look's row and the readiness
    row (/status), the reason a later apply is held back."""
    if how == "its load":
        bg, proc, events, _ = _broken_bg("suggest", RuntimeError(ILLEGAL))
        assert bg.tick() == "banned"
        error = f"RuntimeError: {ILLEGAL}"
    else:
        from test_swipe_auto_failures_room import RUN_ERROR, _run_fails_bg
        bg, proc, events, _, _ = _run_fails_bg("suggest")
        assert bg.tick() == "run"
        error = RUN_ERROR
    told = _faults(events) + [bg.switch.fault, bg.health(), str(bg.status()), bg._fault_why[1]]
    assert _faults(events), "nothing was said"
    _plain(told)
    assert error in capsys.readouterr().out                              # the log keeps it whole
    verb = "load" if how == "its load" else "run"
    # the reason a later apply is held back is quoted inside the suggestion (« Apply now (...) »): no instruction
    # of its own -- Dismiss on a suggestion means « not now »
    assert bg._fault_why[1] == f"the automatic swipe could not {verb} l @ 1280"


def test_an_unknown_load_error_of_the_automatic_swipe_says_plain_words_and_one_thing_to_do():
    """The reviewer's scenario: mode suggest, the second engine fails to load with a CUDA error.  It read
    « (RuntimeError: CUDA error: an illegal memory access was encountered): x @ 1280 still runs - Dismiss lets it
    try again, or set the automatic swipe to off »."""
    bg, proc, events, _ = _broken_bg("suggest", RuntimeError(ILLEGAL))
    assert bg.tick() == "banned"
    assert _faults(events) == ["the automatic swipe could not load l @ 1280 (an error, written in the log): x @ 1280 "
                               "still runs - set the automatic swipe to off"]


# ---------------------------------------------------------------------- 3. the ban names the setting, not the file
def test_the_ban_names_the_setting_never_its_engine_file(_free):
    from test_swipe_auto_live import _bg
    sw, told = _switch_path("an automatic switch's first live frames")
    assert sw.fault == ("the automatic switch to l @ 1920 x 1056 failed (its first live frames failed): x @ 1280 "
                        "still runs - the automatic swipe will not try l @ 1920 x 1056 again until you press Dismiss")
    bg, _, _ = _bg({"v": _live()}, mode="auto")
    bg._banned[L1920.key] = L1920.engine_file                # a failed automatic switch to it
    assert bg.health() == "the automatic swipe will not try l @ 1920 x 1056 again until you press Dismiss"
    assert bg.status()["auto_banned"] == [L1920.engine_file]  # /status's list of files: as before


# ------------------------------------------------------------------------------- 4. one thing to do, each time
@pytest.mark.parametrize("apply", [True, False])
@pytest.mark.parametrize("kind", ["file", "room", "unsure", "other"])
def test_each_failure_says_one_thing_to_do_and_rebuild_only_for_a_file_shown_bad(kind, apply):
    from runtime.seamless_switch import LoadFailure
    todo = LoadFailure("why", kind).todo(L1280, apply=apply)
    assert ", or " not in todo and "if it fails again" not in todo, f"two things to do: {todo!r}"
    assert ("rebuild" in todo) == (kind == "file"), f"{kind}: {todo!r}"
