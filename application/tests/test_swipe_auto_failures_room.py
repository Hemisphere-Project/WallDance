"""P6 review (engines lens, 27/11): two failures the automatic swipe handled or said wrongly.

* TensorRT gives no engine for a bad file AND for a GPU without room for it (mgr-ran, a TensorRT allocator that
  refuses memory: the same AttributeError on ``None.create_execution_context``), and both were said as "the engine
  file could not be read ... rebuild" -- minutes of GPU on show night, for nothing when the GPU is full.  Now the
  file is read (ultralytics' header, TensorRT's own) and so is the GPU's free memory: "rebuild" only for a file
  shown bad, the free MB when the GPU had no room, both named -- a rebuild only if it fails again -- when it
  cannot be told;
* a resident that loads but whose inference fails was run again at every look (24 failing runs in 24 looks, the
  second engine kept, nothing said): now the first failed run bans it as a resident that cannot be loaded, said
  once, and the resident goes to the release.  CPU, with the fakes of ``test_swipe_auto_failures``."""
import json

import pytest

from test_seamless_switch import L1280
from test_swipe_auto_failures import SAID, WAITS, _broken_bg, _faults
from test_swipe_auto_live import _bg, _live, _look_frames, _LookRunner

PLAN = 842393172          # TensorRT's part of yolo11l-pose_1920x1056.engine (mgr-ran, 25/11): a load takes ~1200 MB
NO_ROOM_SAID = ("the automatic swipe could not load l @ 1280 (no room on the GPU: 312 MB free, a load of it takes "
                "about 1200 MB): x @ 1280 still runs - close what else uses the GPU, then Dismiss lets it try again, "
                "or set the automatic swipe to off")
NO_ROOM_WAITS = ("the automatic swipe waits: it could not load l @ 1280 (no room on the GPU: 312 MB free, a load of "
                 "it takes about 1200 MB) - close what else uses the GPU, then Dismiss lets it try again, or set the "
                 "automatic swipe to off")
UNSURE_SAID = ("the automatic swipe could not load l @ 1280 (TensorRT gave no engine, 5000 MB free on the GPU now: "
               "too little room at that moment, or a damaged file): x @ 1280 still runs - close what else uses the "
               "GPU, then Dismiss lets it try again; if it fails again, rebuild yolo11l-pose_1280.engine "
               "(extra/build_engines)")
UNSURE_UNREAD = ("the automatic swipe could not load l @ 1280 (TensorRT gave no engine: too little room on the GPU "
                 "at that moment, or a damaged file): x @ 1280 still runs - close what else uses the GPU, then "
                 "Dismiss lets it try again; if it fails again, rebuild yolo11l-pose_1280.engine (extra/build_engines)")
RUN_ERROR = "inference failed: RuntimeError: TensorRT enqueue failed (injected)"
RUN_SAID = ("the automatic swipe could not run l @ 1280 (inference failed: RuntimeError): x @ 1280 still runs - "
            "Dismiss lets it try again, or set the automatic swipe to off")
RUN_WAITS = ("the automatic swipe waits: it could not run l @ 1280 (inference failed: RuntimeError) - Dismiss lets "
             "it try again, or set the automatic swipe to off")
RUN_OOM = "inference failed: OutOfMemoryError: CUDA out of memory. Tried to allocate 20.00 MiB"
RUN_OOM_SAID = ("the automatic swipe could not run l @ 1280 (no room on the GPU: 312 MB free): x @ 1280 still runs - "
                "close what else uses the GPU, then Dismiss lets it try again, or set the automatic swipe to off")


@pytest.fixture(autouse=True)
def _here(monkeypatch):
    """This machine runs TensorRT 11.3 (the files below say which one built them); the free GPU memory is the
    test's."""
    import runtime.seamless_switch as ss
    monkeypatch.setattr(ss, "_tensorrt_here", lambda: (11, 3), raising=False)
    free = {"mb": None}
    monkeypatch.setattr(ss, "gpu_free_mb", lambda: free["mb"], raising=False)
    return free


def _engine(path, *, plan=PLAN, built=(11, 3), cut=None):
    """An engine file as ultralytics writes it: a 4-byte length, the JSON metadata, then TensorRT's part (b'ftrt',
    its size at bytes 16-23, the TensorRT that built it at 24-25) -- sparse: its size costs no disk."""
    meta = json.dumps({"description": "Ultralytics YOLO11l-pose", "imgsz": [1056, 1920]}).encode()
    head = (b"ftrt" + bytes(4) + (2).to_bytes(8, "little") + plan.to_bytes(8, "little")
            + bytes([built[0], built[1], 0, 0x63]) + b"rtrt")
    with open(path, "wb") as f:
        f.write(len(meta).to_bytes(4, "little") + meta + head)
        f.truncate(4 + len(meta) + (plan if cut is None else cut))
    return str(path)


def _bad_file(tmp_path, how):
    p = tmp_path / "yolo11l-pose_1280.engine"
    if how == "not an engine":                  # what p6-out4/broken.engine holds
        p.write_bytes(b"not a TensorRT engine\n")
    elif how == "zeroed":
        with open(p, "wb") as f:
            f.truncate(PLAN)
    elif how == "cut short":
        _engine(p, cut=PLAN // 3)
    elif how == "another TensorRT":
        _engine(p, built=(11, 0))
    else:                                       # missing
        return str(p)
    return str(p)


def _bg_on(mode, path, exc=None):
    bg, proc, events, loader = _broken_bg(mode, exc)
    bg.engine_path = lambda c: path
    return bg, proc, events, loader


def _looks(bg, proc, n, every=5.0):
    """``n`` looks ``every`` seconds apart, each with a whole new pool of frames with dancers."""
    out = []
    for _ in range(n):
        proc.clock += every
        proc.frames = _look_frames(150, t0=proc.clock - 160.0)
        out.append(bg.tick())
    return out


# ---------------------------------------------------------------------------------------------- finding 1: a load
@pytest.mark.parametrize("how", ["not an engine", "zeroed", "cut short", "another TensorRT", "missing"])
def test_an_engine_file_shown_bad_keeps_the_rebuild_sentence_even_on_a_full_gpu(how, tmp_path, _here):
    """As today (pinned): a file shown bad is the one case told to rebuild -- whatever the GPU's room."""
    _here["mb"] = 100.0
    bg, proc, events, loader = _bg_on("auto", _bad_file(tmp_path, how))
    assert bg.tick() == "banned"                 # loaded before any look now (STANDBY here): it fails there
    assert _faults(events) == [SAID] and bg.status()["last_look"]["outcome"] == WAITS


@pytest.mark.parametrize("mode", ["auto", "suggest"])
def test_a_sound_engine_file_on_a_gpu_without_room_is_said_as_no_room(mode, tmp_path, _here, capsys):
    _here["mb"] = 312.4
    bg, proc, events, loader = _bg_on(mode, _engine(tmp_path / "yolo11l-pose_1280.engine"))
    assert bg.tick() == "banned"
    assert _faults(events) == [NO_ROOM_SAID], "the GPU's room and what to do -- not a rebuild"
    assert bg.switch.health() == NO_ROOM_SAID and "rebuild" not in NO_ROOM_SAID
    assert bg.status()["last_look"]["outcome"] == NO_ROOM_WAITS and bg.health() == NO_ROOM_WAITS
    out = capsys.readouterr().out
    assert "create_execution_context" in out and "the file looks whole" in out          # the log keeps the rest
    assert set(_looks(bg, proc, 20)) == {"banned"} and len(loader.loads) == 1             # banned as before
    assert _faults(events) == [NO_ROOM_SAID]
    bg.switch.dismiss()
    bg.dismiss()                                                                            # Dismiss: once more
    assert _looks(bg, proc, 1) == ["banned"] and len(loader.loads) == 2


@pytest.mark.parametrize("free, said", [(5000.0, UNSURE_SAID), (None, UNSURE_UNREAD)])
def test_when_file_and_room_cannot_be_told_apart_it_names_both_and_no_rebuild_first(free, said, tmp_path, _here):
    """A sound-looking file and room enough now (the memory a failed load took is back, something else may have
    held it then), or no reading: both named; the GPU first, a rebuild only if it fails again."""
    _here["mb"] = free
    bg, proc, events, loader = _bg_on("auto", _engine(tmp_path / "yolo11l-pose_1280.engine"))
    assert bg.tick() == "banned"
    assert _faults(events) == [said]
    assert said.index("close what else uses the GPU") < said.index("rebuild")
    assert "could not be read" not in said


def test_the_operators_apply_reads_the_same_answer(tmp_path, _here):
    """``load_failure`` with the engine's path: the reason and the one thing to do, for the automatic swipe or for
    the operator's Apply (``_prepare``); the old one-argument call reads as before."""
    from runtime.seamless_switch import ENGINE_UNREADABLE, load_failure
    none = AttributeError("'NoneType' object has no attribute 'create_execution_context'")
    _here["mb"] = 312.0
    room = load_failure(none, _engine(tmp_path / "sound.engine"))
    assert room == "no room on the GPU: 312 MB free, a load of it takes about 1200 MB"
    assert room.todo(L1280, apply=True) == "close what else uses the GPU before trying it again"
    no_context = AttributeError("'NoneType' object has no attribute 'set_input_shape'")    # the engine was read
    assert load_failure(no_context, _engine(tmp_path / "sound.engine")) == room
    bad = load_failure(none, _bad_file(tmp_path, "not an engine"))
    assert bad == ENGINE_UNREADABLE and "not a TensorRT engine" in bad.detail
    assert bad.todo(L1280, apply=True) == ("rebuild yolo11l-pose_1280.engine (extra/build_engines) before trying it "
                                           "again")
    _here["mb"] = 5000.0
    assert load_failure(none, _engine(tmp_path / "sound.engine")).todo(L1280, apply=True) == (
        "close what else uses the GPU before trying it again; if it fails again, rebuild yolo11l-pose_1280.engine "
        "(extra/build_engines)")
    oom = load_failure(RuntimeError("CUDA out of memory. Tried to allocate 64.00 MiB"), "any.engine")
    assert oom == "no room on the GPU: 5000 MB free" and oom.kind == "room"
    assert load_failure(none) == ENGINE_UNREADABLE                        # the old call (no path): as before


# ---------------------------------------------------------------------------------------------- finding 2: a run
class _RunFails(_LookRunner):
    """The resident loads and warms, its inference fails (``CandidateRunner.infer`` turns it into ``run.error``)."""

    def __init__(self, error=RUN_ERROR):
        super().__init__()
        self.error, self.runs = error, 0

    def infer(self, images, cand, runner, *, cancelled=None, pace=None, on_frame=None):
        from core.swipe_runner import CandidateRun
        self.runs += 1
        run = CandidateRun(cand)
        run.error = self.error
        return run


def _run_fails_bg(mode, error=RUN_ERROR):
    live = {"v": _live()}
    loader = _RunFails(error)
    bg, proc, events = _bg(live, loader=loader, mode=mode)
    bg.pace_s = 0.0
    proc.swipe_lost_share = lambda: 0.66
    proc.clock = 200.0
    proc.frames = _look_frames(150)
    released = []
    release = bg.switch.release
    bg.switch.release = lambda pair: (released.append(pair), release(pair))[-1]
    return bg, proc, events, loader, released


@pytest.mark.parametrize("mode", ["auto", "suggest"])
def test_a_resident_whose_run_fails_is_banned_at_the_first_failure_and_released(mode, capsys):
    bg, proc, events, loader, released = _run_fails_bg(mode)
    assert bg.tick() == "run" and loader.runs == 1 and len(loader.loads) == 1
    assert _faults(events) == [RUN_SAID], "said once, as the switch's fault"
    assert bg.switch.health() == RUN_SAID
    assert "TensorRT enqueue failed (injected)" in capsys.readouterr().out          # the raw error: the log only
    st = bg.status()
    assert st["last_look"]["outcome"] == RUN_WAITS and "injected" not in str(st)
    assert st["auto_banned"] == [L1280.engine_file] and bg.health() == RUN_WAITS
    assert bg.resident is None and st["resident"] is None, "the second engine is let go"
    assert len(released) == 1 and released[0][0].name == L1280.key          # handed to the switch's release
    # the reviewer's probe: 24 looks, 5 s apart (2 minutes) -- not one more run, load or word
    assert set(_looks(bg, proc, 24)) == {"banned"}
    assert loader.runs == 1 and len(loader.loads) == 1 and bg.inferences == 0 and _faults(events) == [RUN_SAID]
    assert [h["outcome"] for h in bg.history if h["kind"] == "look"].count(RUN_WAITS) == 1


@pytest.mark.parametrize("lift", ["dismiss", "clear_bans"])
def test_dismiss_or_a_project_load_lets_a_failed_run_try_once_more(lift):
    bg, proc, events, loader, released = _run_fails_bg("auto")
    assert bg.tick() == "run" and set(_looks(bg, proc, 5)) == {"banned"} and loader.runs == 1
    bg.switch.dismiss()
    getattr(bg, lift)()
    assert _looks(bg, proc, 1) == ["run"] and loader.runs == 2 and len(loader.loads) == 2   # loaded again: once
    assert set(_looks(bg, proc, 10)) == {"banned"} and loader.runs == 2 and len(released) == 2
    assert _faults(events) == [RUN_SAID, RUN_SAID]


def test_a_run_out_of_gpu_memory_is_said_as_no_room(_here):
    _here["mb"] = 312.0
    bg, proc, events, loader, released = _run_fails_bg("suggest", RUN_OOM)
    assert bg.tick() == "run"
    assert _faults(events) == [RUN_OOM_SAID] and bg.resident is None and len(released) == 1
