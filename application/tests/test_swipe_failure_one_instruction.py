"""P6, the blind re-read of group B2 (finding 2): one instruction a technician alone can follow.

The automatic swipe's second engine that could not be loaded or run ended its alert, its red line and its readiness
row in « close what else uses the GPU, then Dismiss lets it try again » (no room, or a cause it could not tell) --
on the show laptop what else uses the GPU is TouchDesigner, which he must never close -- or in « rebuild <file>
(extra/build_engines), then Dismiss lets it try again », which he cannot do.  Now, whatever the cause, one thing
to do that costs the show nothing: « set Auto to off » (the tracking goes on with the live model).  The cause stays
in the sentence's parentheses; the ban (until Dismiss) is said by the readiness row and the look's row, never by
the alert; the detail -- the error's class and message, the MB a load takes, the file to rebuild -- goes to the log
line and to /status (the switch's ``fault_detail``), never to the screen.

The operator's own Apply keeps a sentence per cause, its one thing to do « keep the current setting »; a file
shown bad is named after it, as information.  CPU, with the fakes of ``test_swipe_auto_failures(_room)``,
``test_swipe_failure_sentences`` and ``test_seamless_switch``; one test on a real ``WallDanceApp``.
"""
import re

import pytest

from test_seamless_switch import L1280, L1920, _Loader, _Port, _live, _sw
from test_swipe_auto_failures import _faults
from test_swipe_auto_failures_room import (RUN_ERROR, RUN_OOM, _NoEngine, _bad_file, _bg_on, _engine,
                                           _run_fails_bg)
from test_swipe_auto_lock import real_app  # noqa: F401 - the fixture
from test_swipe_failure_sentences import ILLEGAL, _apply, _switch_path

NONE = AttributeError("'NoneType' object has no attribute 'create_execution_context'")
ONE = "set Auto to off"
BAN = "will not try it again until you press Dismiss"
# what a technician alone must never be told to do, nor read as the thing to do now
NEVER = ("close what else", "TouchDesigner", "extra/build_engines", "Dismiss lets it try again")
CLASSY = re.compile(r"Error\b|Exception\b")


@pytest.fixture(autouse=True)
def _here(monkeypatch):
    """This machine runs TensorRT 11.3; the free GPU memory is the test's (a CPU test has no CUDA)."""
    import runtime.seamless_switch as ss
    monkeypatch.setattr(ss, "_tensorrt_here", lambda: (11, 3), raising=False)
    free = {"mb": 312.4}
    monkeypatch.setattr(ss, "gpu_free_mb", lambda: free["mb"], raising=False)
    return free


def _instruction(text: str) -> str:
    """The one thing to do: what follows the sentence's last « - »."""
    assert " - " in text, f"no instruction: {text!r}"
    return text.rsplit(" - ", 1)[1]


def _never(texts, where):
    for t in texts:
        bad = [w for w in NEVER if w in t]
        assert not bad, f"{where}: {bad} in {t!r}"
        assert not _instruction(t).startswith("rebuild"), f"{where}: told to rebuild now: {t!r}"
        assert not CLASSY.search(t), f"{where}: a Python class on screen: {t!r}"


# every failure of the background's own load or run: (what makes it, a word of the cause the operator reads,
# words of the detail that only the log and /status hold)
BACKGROUND = {
    "load, no room (a sound file, 312 MB free)": (
        "load", lambda tmp: (_engine(tmp / "yolo11l-pose_1280.engine"), 312.4, NONE),
        "no room on the GPU: 312 MB free", ("create_execution_context", "a load takes about")),
    "load, out of memory": (
        "load", lambda tmp: (_engine(tmp / "yolo11l-pose_1280.engine"), 312.4,
                             RuntimeError("CUDA out of memory. Tried to allocate 64.00 MiB")),
        "no room on the GPU: 312 MB free", ("RuntimeError: CUDA out of memory", "a load of it takes about 1200 MB")),
    "load, not told (room free now)": (
        "load", lambda tmp: (_engine(tmp / "yolo11l-pose_1280.engine"), 5000.0, NONE),
        "too little room at that moment, or a damaged file",
        ("create_execution_context", "if it fails again with room free, rebuild yolo11l-pose_1280.engine")),
    "load, not told (no reading)": (
        "load", lambda tmp: (_engine(tmp / "yolo11l-pose_1280.engine"), None, NONE),
        "too little room on the GPU at that moment, or a damaged file", ("free GPU memory not known",)),
    "load, the file shown bad (not an engine)": (
        "load", lambda tmp: (_bad_file(tmp, "not an engine"), 100.0, NONE),
        "the engine file could not be read",
        ("the file: not a TensorRT engine", "rebuild yolo11l-pose_1280.engine (extra/build_engines)")),
    "load, the file shown bad (cut short)": (
        "load", lambda tmp: (_bad_file(tmp, "cut short"), 100.0, NONE),
        "the engine file could not be read", ("cut short", "rebuild yolo11l-pose_1280.engine (extra/build_engines)")),
    "load, the file shown bad (missing)": (
        "load", lambda tmp: (_bad_file(tmp, "missing"), 100.0, NONE),
        "the engine file could not be read", ("cannot be opened", "rebuild yolo11l-pose_1280.engine")),
    "load, another error": (
        "load", lambda tmp: (_engine(tmp / "yolo11l-pose_1280.engine"), 312.4, RuntimeError(ILLEGAL)),
        "an error, written in the log", (f"RuntimeError: {ILLEGAL}",)),
    "run, an error": ("run", RUN_ERROR, "the engine stopped during a run", ("TensorRT enqueue failed (injected)",)),
    "run, out of memory": ("run", RUN_OOM, "no room on the GPU: 312 MB free",
                           ("OutOfMemoryError: CUDA out of memory",)),
}


def _background(how, mode, tmp_path, here):
    verb, make, cause, detail = BACKGROUND[how]
    if verb == "load":
        path, here["mb"], exc = make(tmp_path)
        bg, proc, events, loader = _bg_on(mode, path, exc)
        assert bg.tick() == "banned"
    else:
        here["mb"] = 312.0
        bg, proc, events, loader, released = _run_fails_bg(mode, make)
        assert bg.tick() == "run"
    return bg, events, verb, cause, detail


@pytest.mark.parametrize("mode", ["suggest", "auto"])
@pytest.mark.parametrize("how", list(BACKGROUND))
def test_every_background_failure_says_one_instruction_set_auto_to_off(how, mode, tmp_path, _here, capsys):
    """The alert and the red line (the switch's fault: the same text), the readiness row and the look's row end in
    « set Auto to off »; the alert says nothing more to do -- no Dismiss, no GPU to free, no rebuild."""
    bg, events, verb, cause, _ = _background(how, mode, tmp_path, _here)
    alert, = _faults(events)                              # the alert and the red line: this one text
    row, look = bg.switch.health(), bg.status()["last_look"]["outcome"]
    assert alert.startswith(f"the automatic swipe could not {verb} l @ 1280 (") and \
        alert.endswith(f"): x @ 1280 still runs - {ONE}"), alert
    assert f"({cause}" in alert or f"{cause})" in alert, f"the cause left the parentheses: {alert!r}"
    for text in (alert, row, look, bg.health()):
        assert _instruction(text) == ONE, f"{how}: not the one instruction: {text!r}"
    assert "Dismiss" not in alert, f"the alert carries more than the one instruction: {alert!r}"
    _never([alert, row, look, bg.health()], how)


@pytest.mark.parametrize("how", list(BACKGROUND))
def test_the_ban_stays_said_by_the_readiness_row_and_the_looks_row(how, tmp_path, _here):
    bg, events, verb, cause, _ = _background(how, "suggest", tmp_path, _here)
    alert, = _faults(events)
    head = alert.split(": x @ 1280 still runs - ")[0]          # « the automatic swipe could not load l (why) »
    assert bg.switch.health() == f"{head} and {BAN}: x @ 1280 still runs - {ONE}"
    assert bg.status()["last_look"]["outcome"] == bg.health() and BAN in bg.health()
    assert bg.held(), "the ban no longer holds until Dismiss"
    bg.switch.dismiss()
    bg.dismiss()                                          # Dismiss: the ban goes, as before
    assert not bg.held() and bg.health() is None and bg.switch.health() is None


@pytest.mark.parametrize("how", list(BACKGROUND))
def test_the_detail_goes_to_the_log_line_and_to_status_never_to_the_screen(how, tmp_path, _here, capsys):
    """The class and message, the MB a load takes, the file to rebuild: in the log line and in /status's
    ``swipe.switch.fault_detail``, beside the fault text; the background's own /status keeps none of it."""
    bg, events, verb, cause, detail = _background(how, "suggest", tmp_path, _here)
    st = bg.switch.status()
    assert st.get("fault_detail"), f"{how}: /status has no detail beside the fault: {st}"
    assert st["fault"] == _faults(events)[0]
    log = capsys.readouterr().out
    for words in detail:
        assert words in st["fault_detail"], f"{how}: {words!r} not in /status's detail {st['fault_detail']!r}"
        assert words in log, f"{how}: {words!r} not in the log"
        for shown in (st["fault"], bg.switch.health(), bg.health(), str(bg.status())):
            assert words not in shown, f"{how}: the detail {words!r} reached the screen: {shown!r}"
    bg.switch.dismiss()
    assert bg.switch.status()["fault"] is None and bg.switch.status()["fault_detail"] is None


# ------------------------------------------------------------------------------------- the operator's own Apply
def _apply_load(tmp_path, here, how):
    sound = _engine(tmp_path / "yolo11l-pose_1920x1056.engine")
    path, here["mb"] = {"no room": (sound, 312.0), "not told": (sound, 5000.0),
                        "a file shown bad": (_bad_file(tmp_path, "not an engine"), 312.0)}[how]
    live, port = {"v": _live()}, _Port()
    sw, _, events = _sw(live, loader=_NoEngine(NONE), port=port)
    sw.engine_path = lambda c: path
    _apply(sw)
    return sw


def _apply_runs_pytorch(monkeypatch):
    import runtime.seamless_switch as ss
    monkeypatch.setattr(ss, "runs_tensorrt", lambda model: False)
    live, port = {"v": _live()}, _Port()
    sw, _, _ = _sw(live, loader=_Loader(), port=port)
    _apply(sw)
    return sw


APPLY = ["load: no room", "load: not told", "load: a file shown bad", "load: another error",
         "warm-up: out of memory", "warm-up: another error", "warm-up: runs PyTorch", "the swap refused",
         "its first live frames"]


@pytest.mark.parametrize("how", APPLY)
def test_every_apply_failure_says_keep_the_current_setting_never_close_or_rebuild(how, tmp_path, _here,
                                                                                 monkeypatch):
    if how.startswith("load: ") and how != "load: another error":
        sw = _apply_load(tmp_path, _here, how[6:])
    elif how == "warm-up: runs PyTorch":
        sw = _apply_runs_pytorch(monkeypatch)
    else:
        sw, _ = _switch_path({"load: another error": "a load error", "warm-up: out of memory":
                              "a warm-up out of memory", "warm-up: another error": "a warm-up error"}.get(how, how))
    text = sw.fault
    assert text and text.startswith("the switch to l @ 1920 x 1056 failed"), text
    todo = _instruction(text)
    assert todo.split(";")[0] == "keep the current setting", f"{how}: {text!r}"
    _never([text, sw.health()], how)
    shown_bad = how in ("load: a file shown bad", "warm-up: runs PyTorch")
    assert todo == ("keep the current setting" + (f"; the engine file needs a rebuild: {L1920.engine_file}"
                                                  if shown_bad else "")), f"{how}: {text!r}"
    assert sw.status()["fault_detail"], f"{how}: /status has no detail beside the fault"
    if shown_bad:
        assert f"rebuild {L1920.engine_file} (extra/build_engines)" in sw.status()["fault_detail"]


def test_the_load_failures_one_thing_to_do_whatever_the_cause():
    from runtime.seamless_switch import LoadFailure
    for kind in ("file", "room", "unsure", "other"):
        assert LoadFailure("why", kind).todo(L1280) == ONE
        assert LoadFailure("why", kind).todo(L1280, apply=True) == (
            "keep the current setting" + ("; the engine file needs a rebuild: yolo11l-pose_1280.engine"
                                          if kind == "file" else ""))


# ------------------------------------------------------------------------------- on the app: alert, line, row
def test_on_the_app_the_alert_and_the_red_line_say_one_instruction_the_row_the_ban(real_app, tmp_path,  # noqa: F811
                                                                                  _here):
    """A real ``WallDanceApp`` handed the background's fault (no room): the alert and the red line end in « set Auto
    to off » and say no more; the readiness row says the ban too; /status keeps the detail beside the fault."""
    app, out = real_app
    bg, events, *_ = _background("load, no room (a sound file, 312 MB free)", "suggest", tmp_path, _here)
    fault = bg.switch.fault
    app.swipe_bg._banned.update(bg._banned)               # the app's own looker, as after that failed load
    app.swipe_bg._unloadable.update(bg._unloadable)
    app.swipe_bg.auto.mode = "suggest"
    del out[:]
    app.swipe_switch.fail(fault)
    alert = [e.message for e in out if type(e).__name__ == "Alert"]
    line = [e.text for e in out if type(e).__name__ == "SettingsSwipeProgress" and e.state == "apply_failed"]
    assert alert == line == [str(fault)] and _instruction(alert[0]) == ONE and "Dismiss" not in alert[0]
    row = app._swipe_readiness_row()
    assert BAN in row.detail and _instruction(row.detail) == ONE, row.detail
    _never(alert + line + [row.detail], "the app")
    st = app._remote_status()["swipe"]["switch"]
    assert st["fault"] == str(fault) and "create_execution_context" in st["fault_detail"]
