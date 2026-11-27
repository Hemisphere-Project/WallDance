"""P6 (Plan Cordee): WHEN the automatic swipe's second engine is loaded and released (blind review 9326381, lens
loop findings 1-2, lens engines finding 4 and nit 6; reread 0c777dd nits 3-4).

A TensorRT load holds the GIL in slices of 54-80 ms and a release about 50 ms: 4 to 6 live frames slowed, or one
late.  So both happen only where a pause does not show -- out of RUN, or in RUN on a wall empty for about 2 s (no
track, no identity slot held) -- never on entering RUN, never while a dancer is tracked; a look that falls due
without the engine while a dancer is tracked waits, and says so; no idle timer while the mode is on (it freed and
reloaded the engine about every 30 minutes of a settled show, with dancers on the wall); off ends P6 for the
session: its thread, and its engine at once out of RUN (after a live frame in RUN).  The readiness report says
what the engine holds.

CPU, with the fakes of ``test_swipe_auto_live``.  The new arguments (``in_run``, ``wall_empty``, ``gpu_used``) are
set on the object once it is built, so each test also runs on the build before this fix and fails there for the
reason it names.
"""
import inspect
import threading
import time
import weakref
from types import SimpleNamespace

import pytest

from core.swipe import Candidate
from test_seamless_switch import L1280, L1920, _live
from test_swipe_auto_live import _bg, _look_frames, _LookRunner

WAITS = "the automatic swipe waits for an empty wall to load l @ 1280"
X1920 = Candidate("yolo11x-pose", 1280, (1920, 1056))


class _Wall:
    """What the app tells the looker: RUN or not, the wall empty or a dancer tracked."""

    def __init__(self, run=False, empty=True):
        self.run, self.empty = run, empty


def _setup(mode="off", run=False, empty=True, frames=0, keep=False):
    live = {"v": _live()}
    loader = _LookRunner()
    if keep:
        loader.infer_boxes = []          # the other model sees nobody the live one missed: every look keeps
    bg, proc, events = _bg(live, loader=loader, mode=mode)
    bg.pace_s = 0.0
    proc.swipe_lost_share = lambda: 0.66
    proc.clock = 200.0
    wall = _Wall(run, empty)
    bg.in_run = lambda: wall.run
    bg.wall_empty = lambda: wall.empty
    if frames:
        proc.frames = _look_frames(frames, t0=proc.clock - frames - 10.0)
    return SimpleNamespace(bg=bg, proc=proc, events=events, loader=loader, live=live, wall=wall)


def _kinds(e, *kinds):
    return [h["kind"] for h in e.bg.history if h["kind"] in kinds]


def _until(cond, timeout=3.0):
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ------------------------------------------------------------------ the settled night (review: engines finding 4)
def test_a_settled_night_loads_the_second_engine_once_and_never_frees_it():
    """The reviewer's probe (gates/p6review/engines/probe_settled_night.py): 2 h of loop clock, dancers tracked
    all along, every look « keep » -- the keep back-off (300 s doubling to 1,800 s) outlasted the 600 s idle
    release: 4 loads and 4 releases during the dancing.  Now: ONE load (at the mode, in STANDBY), no release."""
    e = _setup(keep=True)
    e.bg.set_mode("auto")                               # in STANDBY, before the show
    e.bg.tick()
    e.wall.run, e.wall.empty = True, False              # RUN, dancers on the wall all night
    t_end = e.proc.clock + 2 * 3600.0
    runs = []
    while e.proc.clock < t_end:
        e.proc.clock += 5.0
        e.proc.frames = _look_frames(300, lost=30, t0=e.proc.clock - 300.0)    # 5 minutes of dancing kept
        if e.bg.tick() in ("run", "probe"):
            runs.append(e.proc.clock)
    advices = [h.get("advice") for h in e.bg.history if h["kind"] == "advice"]
    assert len(runs) >= 5 and set(advices) == {"none"}  # the looks ran, and kept the live setting
    assert _kinds(e, "load") == ["load"] and _kinds(e, "release") == [], (
        f"loads and releases during the dancing: {_kinds(e, 'load', 'release')}")
    assert len(e.loader.loads) == 1 and e.bg.resident is not None


def test_the_second_engine_has_no_idle_timer():
    """An hour of empty wall in RUN after a look: the engine stays (it went after 600 s without a run due)."""
    e = _setup(mode="suggest", frames=150)
    assert e.bg.tick() == "run" and e.bg.resident is not None
    e.proc.frames = []
    e.wall.run = True
    for _ in range(720):
        e.proc.clock += 5.0
        e.bg.tick()
    assert e.bg.resident is not None and _kinds(e, "release") == [], "released on an idle timer"


# ------------------------------------------------------------------ the moments (review: loop findings 1-2)
def test_the_mode_set_mid_show_loads_the_second_engine_at_once_whatever_is_on_the_wall():
    """The operator sets the mode in RUN with dancers tracked: his click is the moment -- one load at the look it
    wakes, the look runs; then no other load and no release while the dancers stay."""
    e = _setup(run=True, empty=False, frames=150)
    e.bg.set_mode("suggest")
    assert e.bg.tick() == "run" and e.loader.loads == [L1280.engine_file], "the mode's click did not load it"
    assert WAITS not in [h.get("outcome") for h in e.bg.history if h["kind"] == "look"]
    for _ in range(40):
        e.proc.clock += 5.0
        e.proc.frames = _look_frames(150, t0=e.proc.clock - 160.0)
        e.bg.tick()
    assert _kinds(e, "load", "release") == ["load"] and len(e.loader.loads) == 1


def test_run_entered_without_the_second_engine_and_a_dancer_tracked_waits_for_an_empty_wall():
    """No engine when RUN begins (STANDBY's looks taken by a calibration), dancers tracked: a look falls due,
    nothing loads -- entering RUN is not a moment, nor is the mode set long before -- one row says why; the wall
    empty for about 2 s: one load, the row goes, the look runs."""
    e = _setup(mode="suggest", frames=150)
    e.bg.busy = lambda: "a calibration"
    assert e.bg.tick() == "busy" and e.loader.loads == []
    e.bg.busy = lambda: None
    e.wall.run, e.wall.empty = True, False              # RUN begins with dancers on the wall
    assert e.bg.tick() == "waiting" and e.loader.loads == [], "loaded with a dancer tracked"
    assert e.bg.status()["last_look"]["outcome"] == WAITS and e.loader.infers == 0
    e.proc.clock += 5.0
    assert e.bg.tick() == "waiting" and e.loader.loads == []
    assert [h["outcome"] for h in e.bg.history if h["kind"] == "look"] == [WAITS]      # said once
    e.wall.empty = True                                 # the dancers leave
    e.proc.clock += 5.0
    assert e.bg.tick() == "waiting" and e.loader.loads == []                        # empty for 0 s: not yet
    assert 0.25 <= e.bg._next_s <= 2.0                  # the next look comes when 2 s have passed
    e.proc.clock += 1.5
    assert e.bg.tick() == "waiting" and e.loader.loads == []                        # 1.5 s
    e.proc.clock += 0.5
    assert e.bg.tick() == "run" and e.loader.loads == [L1280.engine_file]           # 2 s: loaded, the look runs
    assert e.bg.status()["last_look"]["outcome"] != WAITS
    assert [h["outcome"] for h in e.bg.history if h["kind"] == "look"][0] == WAITS


def test_the_wait_row_goes_when_the_mode_goes_off():
    e = _setup(mode="auto", run=True, empty=False, frames=150)          # (the mode set long before: no moment)
    assert e.bg.tick() == "waiting" and e.bg.status()["last_look"]["outcome"] == WAITS
    e.bg.set_mode("off")
    assert e.bg.status()["last_look"] is None, "it still says it waits"


def test_entering_run_loads_nothing():
    """STANDBY taken by a calibration (no load then), RUN begins on an empty wall with a look due: nothing loads in
    RUN's first 2 s -- it loaded at the first RUN look."""
    e = _setup(mode="suggest", frames=150)
    e.bg.busy = lambda: "a calibration"
    assert e.bg.tick() == "busy" and e.loader.loads == []
    e.bg.busy = lambda: None
    e.wall.run = True                                   # RUN begins; the wall is empty
    assert e.bg.tick() == "waiting" and e.loader.loads == [], "loaded on entering RUN"
    e.proc.clock += 1.0
    assert e.bg.tick() == "waiting" and e.loader.loads == []
    e.proc.clock += 1.0
    assert e.bg.tick() == "run" and e.loader.loads == [L1280.engine_file]


def test_standby_with_the_mode_on_loads_the_second_engine():
    """Out of RUN a pause does not show: the engine loads at the first look, before any dancing is kept."""
    e = _setup(mode="suggest")
    assert e.bg.tick() == "idle"
    assert e.loader.loads == [L1280.engine_file] and e.bg.resident.candidate == L1280 and e.loader.infers == 0


def test_an_empty_wall_run_costs_one_load_at_the_mode_and_no_inference():
    """120 s of RUN on an empty wall with the mode set at its start: one load (at the mode), no inference, no
    release."""
    e = _setup(run=True, empty=True)
    e.bg.set_mode("suggest")
    for _ in range(25):
        e.bg.tick()
        e.proc.clock += 5.0
    assert e.loader.loads == [L1280.engine_file], "the second engine is not loaded at the mode's moment"
    assert e.loader.infers == 0 and e.bg.inferences == 0 and _kinds(e, "release") == []


@pytest.mark.parametrize("change", ["project", "input"])
def test_a_project_load_or_an_input_change_swaps_the_second_engine_at_that_moment(change):
    """STANDBY, a hand reload whose new model has not run a frame (the switch's safe window shut): the old
    engine goes while the model loads -- not at the first RUN frame (three engines until then) -- and the new
    one comes as soon as the new setting is live."""
    from runtime.swipe_background import Resident
    e = _setup(mode="suggest")
    e.proc._swipe_key = None
    e.bg.resident = Resident(L1280, "m-old", "r-old", e.bg._resident_key(e.live["v"]), 0.4)
    e.live["v"] = None                                  # the load
    assert e.bg.tick() == "loading"
    assert e.bg.resident is None, "the old engine waits beside the reload"
    e.live["v"] = _live(project="another") if change == "project" else _live(cand=X1920, model_id=2)
    assert e.bg.tick() == "idle"
    want = L1280 if change == "project" else L1920
    assert e.bg.resident.candidate == want and e.bg.resident.key == e.bg._resident_key(e.live["v"])
    assert _kinds(e, "load", "release") == ["release", "load"] and e.loader.loads == [want.engine_file]


def test_a_stale_resident_is_never_replaced_while_a_dancer_is_tracked():
    """In RUN with a dancer, a resident that no longer fits (another project) and a look due, the switch's safe
    window shut: no load and no release then (the old one was freed unpaced on the looker beside the new load);
    on the empty wall: the old one goes, then the new one loads."""
    from runtime.swipe_background import Resident
    e = _setup(mode="suggest", run=True, empty=False, frames=150)
    e.proc._swipe_key = None
    key = e.bg._resident_key(e.live["v"])
    stale = Resident(L1280, "m-old", "r-old", ("another project",) + tuple(key[1:]), 0.4)
    e.bg.resident = stale
    assert e.bg.tick() == "waiting"
    assert e.bg.resident is stale and e.loader.loads == [], "replaced with a dancer tracked"
    e.wall.empty = True
    e.proc.clock += 5.0
    e.bg.tick()
    e.proc.clock += 2.0
    assert e.bg.tick() == "run" and e.bg.resident.key == key
    assert _kinds(e, "load", "release") == ["release", "load"]


def test_after_the_modes_load_a_resident_made_stale_mid_show_waits_for_an_empty_wall():
    """The mode's click is one moment, used once: the engine it loaded made stale later in the show (another
    project, the live model not reloaded), a dancer tracked and a look due: no release, no load, the row says
    why; on the empty wall: the old one goes, then the new one loads."""
    from runtime.swipe_background import Resident
    e = _setup(run=True, empty=False, frames=150)
    e.proc._swipe_key = None                            # the switch's safe window shut
    e.bg.set_mode("suggest")
    assert e.bg.tick() == "run" and len(e.loader.loads) == 1          # the mode's moment
    key = e.bg._resident_key(e.live["v"])
    stale = Resident(L1280, "m-old", "r-old", ("another project",) + tuple(key[1:]), 0.4)
    e.bg.resident = stale
    outs = []
    for _ in range(4):
        e.proc.clock += 400.0
        e.proc.frames = _look_frames(150, t0=e.proc.clock - 160.0)
        outs.append(e.bg.tick())
    assert "waiting" in outs and e.bg.status()["last_look"]["outcome"] == WAITS, outs
    assert e.bg.resident is stale and len(e.loader.loads) == 1, "replaced with a dancer tracked"
    assert _kinds(e, "load", "release") == ["load"]
    e.wall.empty = True
    e.proc.clock += 5.0
    e.bg.tick()
    e.proc.clock += 2.0
    e.bg.tick()
    assert e.bg.resident is not stale and e.bg.resident.key == key and len(e.loader.loads) == 2
    assert _kinds(e, "load", "release") == ["load", "release", "load"]


# ------------------------------------------------------------------ off ends P6 (reread 0c777dd nits 3-4)
def test_mode_off_ends_the_looker_and_frees_the_engine_at_once_in_standby():
    """STANDBY after a hand reload (no frame of the new model yet): off frees the engine at once -- it stayed until
    the first RUN frame -- and the looker's thread ends (it ticked every 5 s for the session); a mode again starts
    it again and the engine comes back; quit frees it."""
    from runtime.swipe_background import BackgroundSwipe, Resident
    e = _setup(mode="suggest")
    e.proc._swipe_key = None
    e.bg.resident = Resident(L1280, "m", "r", e.bg._resident_key(e.live["v"]), 0.4)
    BackgroundSwipe.start(e.bg)                         # the real thread (looks every 999 s here)
    t = e.bg._thread
    e.bg.set_mode("off")
    assert _until(lambda: not t.is_alive()), "the looker still runs after off"
    assert e.bg.resident is None and e.bg._thread is None
    assert [h.get("purpose") for h in e.bg.history if h["kind"] == "release"] == ["standby"]
    e.bg.set_mode("auto")                               # on again
    BackgroundSwipe.start(e.bg)
    assert _until(lambda: e.bg.resident is not None) and e.bg._thread.is_alive()
    assert e.loader.loads == [L1280.engine_file]
    e.bg.stop(2.0)                                      # quit
    assert e.bg.resident is None and e.bg.history[-1]["purpose"] == "quit"


def test_mode_off_in_run_frees_the_engine_after_a_live_frame():
    """Regression guard: in RUN, off still frees the engine in the switch's safe window (right after a live frame),
    not on the looker at once."""
    from runtime.swipe_background import Resident
    e = _setup(mode="suggest", run=True, empty=False)
    e.bg.resident = Resident(L1280, "m", "r", e.bg._resident_key(e.live["v"]), 0.4)
    calls = []
    real = e.bg.switch.free_pending
    e.bg.switch.free_pending = lambda extra=None: calls.append(extra) or real(extra)
    e.bg.set_mode("off")
    assert e.bg.tick() == "off"
    assert e.bg.resident is None and calls and calls[0] is not None


# ------------------------------------------------------------------ quit (the switch's note at e017a33)
class _Engine:
    """Stands for an engine: which thread frees it (the last reference goes)."""

    def __init__(self, freed):
        weakref.finalize(self, lambda: freed.append(threading.current_thread().name))


@pytest.mark.parametrize("load_s, join_s", [(0.3, 3.0), (None, 0.2)])
def test_a_load_in_progress_at_quit_is_dropped_not_kept(load_s, join_s, capsys):
    """The app quits while the looker loads the second engine (it cannot be cut short).  The join outlasts the
    load: the engine is gone when ``stop`` returns (before the switch's stop).  The load outlasts the join:
    ``stop`` frees nothing and says so; the looker drops what it loaded instead of keeping it as the resident."""
    from runtime.swipe_background import BackgroundSwipe
    e = _setup(mode="suggest")                          # STANDBY: the first look loads
    began, go, freed = threading.Event(), threading.Event(), []
    real = e.loader.load

    def slow_load(path, cand, model=None):
        _, runner, s = real(path, cand, model)
        began.set()
        if load_s is None:
            go.wait(5.0)                                # until the test lets it end
        else:
            time.sleep(load_s)
        return _Engine(freed), runner, s
    e.loader.load = slow_load
    BackgroundSwipe.start(e.bg)
    t = e.bg._thread
    assert began.wait(3.0), "the first look did not load"
    e.bg.stop(join_s)
    if load_s is not None:
        assert freed and e.bg.resident is None, "still held when stop returned"
    else:
        assert t.is_alive() and freed == [] and "still in a look at quit" in capsys.readouterr().out
        go.set()                                        # the load ends after the quit
        assert _until(lambda: not t.is_alive()) and freed, "what loaded after the quit was kept"
    assert e.bg.resident is None and not t.is_alive()
    assert [h.get("purpose") for h in e.bg.history if h["kind"] == "release"] == ["quit"]
    assert _kinds(e, "load") == [], "kept as the resident before it went"


def test_quit_frees_the_resident_at_once_never_waiting_for_a_frames_end():
    """In RUN, a dancer on, the loop running (a frame's end would be waited for up to 0.2 s): ``stop`` -- called
    on the loop thread -- frees the resident on its own thread, under the engine lock, at once; the switch's
    paced window is not used."""
    from runtime.swipe_background import BackgroundSwipe, Resident
    e = _setup(mode="suggest", run=True, empty=False)
    freed = []
    e.bg.resident = Resident(L1280, _Engine(freed), "r", e.bg._resident_key(e.live["v"]), 0.4)
    BackgroundSwipe.start(e.bg)
    assert _until(lambda: e.bg.ticks >= 1)              # its first look: the resident fits, kept
    assert e.bg.resident is not None and freed == []
    e.proc.frame_end, e.proc.frame_end_t = threading.Event(), 0.0
    e.proc.frame_done_t = time.perf_counter()           # a live pass just now: a frame's end is coming
    paced = []
    e.bg.switch.free_pending = lambda extra=None: paced.append(extra)
    locked = []
    lock = e.bg.engine_lock
    e.bg.engine_lock = SimpleNamespace(acquire=lambda *a, **k: locked.append(1) or lock.acquire(*a, **k),
                                       release=lock.release)
    t0 = time.perf_counter()
    e.bg.stop(3.0)
    dt = time.perf_counter() - t0
    assert e.bg.resident is None and freed == [threading.current_thread().name], "not freed here, at once"
    assert dt < 0.1, f"stop took {1000 * dt:.0f} ms: it waited for a frame's end"
    assert paced == [] and locked, "paced, or freed outside the engine lock"
    assert e.bg.history[-1]["kind"] == "release" and e.bg.history[-1]["purpose"] == "quit"


# ------------------------------------------------------------------ the cost, said once
def test_the_readiness_row_says_what_the_second_engine_holds():
    """While the key is not off and the engine is loaded: one information row with its GPU memory, measured around
    the load (10 MB steps); nothing with the key off."""
    pytest.importorskip("dearpygui")
    import app as appmod
    e = _setup(mode="suggest", frames=150)              # (a look due too: it loads on either build)
    used = iter([1000 * 2 ** 20, 1234 * 2 ** 20])
    e.bg.gpu_used = lambda: next(used)
    e.bg.tick()
    assert e.bg.resident is not None
    assert getattr(e.bg.resident, "mb", None) == 230, "what the engine holds is not measured"
    assert e.bg.status()["resident_mb"] == 230
    fake = SimpleNamespace(swipe_bg=e.bg)
    row = appmod.WallDanceApp._swipe_resident_row(fake)
    assert (row.name, row.status, row.detail) == (
        "auto swipe", "ok", "the automatic swipe keeps a second model loaded: about 230 MB")
    assert "self._swipe_resident_row()" in inspect.getsource(appmod.WallDanceApp._build_readiness_report)
    e.bg.set_mode("off")
    assert appmod.WallDanceApp._swipe_resident_row(fake) is None    # the key off: no row (it is going)
    from core.ops_monitor import check_swipe_resident
    assert check_swipe_resident(swipe_auto="auto", resident="l @ 1280").detail == (
        "the automatic swipe keeps a second model loaded")       # not measured (no GPU): no number
    assert check_swipe_resident(swipe_auto="auto", resident=None) is None
