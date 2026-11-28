"""P6 (Plan Cordee): how the automatic swipe behaves and speaks when something fails.

* a resident that cannot be loaded (a broken engine file, no GPU memory left beside TouchDesigner) is tried ONCE:
  the setting is banned -- no load, no inference, no look for it -- until Dismiss or a project load; it is said
  once, as the switch's fault (the visible path of a failed automatic switch), in plain words; the raw error goes
  to the log; while it holds, the look says why the swipe waits (was: a new load at every look, 16 in 41 s on the
  GPU, the raw AttributeError in /status, nothing on screen);
* the suggestion an apply becomes after a failure says the winner once, and says whose switch failed (what lifts
  it is the fault's own words: Dismiss on a suggestion means « not now »).  CPU, with the fakes of
  ``test_swipe_auto_live`` and ``test_swipe_auto_rollback``."""
import pytest

from test_seamless_switch import L1280
from test_swipe_auto_live import _bg, _look_frames, _LookRunner, _live
from test_swipe_auto_rollback import _first_frame_raises, _look, _next_runs, _setup, _two_looks_ask_the_switch
from test_swipe_auto_live import swipe_auto_unlocked  # noqa: E402,F401 - the fixture
from test_swipe_auto_live import swipe_auto_apply_released  # noqa: F401 - the fixture

# mode auto set on the BackgroundSwipe itself, past the app's doors: the release list must name it too (the
# last guard before a switch, since 27/11); the build ships locked
pytestmark = pytest.mark.usefixtures("swipe_auto_unlocked")

RAW = "'NoneType' object has no attribute 'create_execution_context'"
SAID = ("the automatic swipe could not load l @ 1280 (the engine file could not be read): x @ 1280 still runs - "
        "set Auto to off")
ROW = ("the automatic swipe could not load l @ 1280 (the engine file could not be read) and will not try it again "
       "until you press Dismiss: x @ 1280 still runs - set Auto to off")
WAITS = ("the automatic swipe waits: it could not load l @ 1280 (the engine file could not be read) and will not try "
         "it again until you press Dismiss - set Auto to off")
OOM = "CUDA out of memory. Tried to allocate 64.00 MiB"
SAID_OOM = ("the automatic swipe could not load l @ 1280 (no room on the GPU: 312 MB free): x @ 1280 still runs - "
            "set Auto to off")
AUTO_FAILED = "the last automatic switch failed"
APPLY_FAILED = "the last Apply failed"
SAYS = "l @ 1280 covers 100 % of the dancers, x @ 1280 33 %"


class _BrokenRunner(_LookRunner):
    """The other model's engine does not load (TensorRT returns no engine: ultralytics fails on it)."""

    def __init__(self, exc=None):
        super().__init__()
        self.exc = exc or AttributeError(RAW)

    def load(self, path, cand, model=None):
        self.loads.append(path)
        raise self.exc


def _broken_bg(mode, exc=None):
    live = {"v": _live()}
    loader = _BrokenRunner(exc)
    bg, proc, events = _bg(live, loader=loader, mode=mode)
    bg.pace_s = 0.0
    proc.swipe_lost_share = lambda: 0.66
    proc.clock = 200.0
    proc.frames = _look_frames(150)
    return bg, proc, events, loader


def _faults(events):
    return [p["text"] for k, p in events if k == "fault"]


def _ticks(bg, proc, n):
    """``n`` looks, a minute apart, each with a whole new pool of frames with dancers."""
    out = []
    for _ in range(n):
        proc.clock += 60.0
        proc.frames = _look_frames(150, t0=proc.clock - 160.0)
        out.append(bg.tick())
    return out


@pytest.mark.parametrize("mode", ["auto", "suggest"])
def test_a_resident_that_cannot_be_loaded_is_tried_once_and_said_once(mode, capsys):
    bg, proc, events, loader = _broken_bg(mode)
    assert bg.tick() == "banned"                 # loaded before any look now (STANDBY here): it fails there
    assert loader.loads == [L1280.engine_file] and loader.infers == 0 and bg.inferences == 0
    assert _faults(events) == [SAID], "said once, as the switch's fault (the visible path of a failed switch)"
    assert bg.switch.fault == SAID and bg.switch.health() == ROW
    assert [k for k, _ in events] == ["fault"]                       # no suggestion, no switch
    assert RAW in capsys.readouterr().out                            # the raw error: the log only
    st = bg.status()
    assert st["last_look"]["outcome"] == WAITS and RAW not in str(st)
    assert st["auto_banned"] == [L1280.engine_file]
    # banned: no load, no inference, no look run for it, nothing said again -- over many looks
    assert set(_ticks(bg, proc, 20)) == {"banned"}
    assert len(loader.loads) == 1 and loader.infers == 0 and bg.inferences == 0 and _faults(events) == [SAID]
    assert bg.status()["last_look"]["outcome"] == WAITS
    assert [h["outcome"] for h in bg.history if h["kind"] == "look"] == [WAITS]      # one look row, not 21


@pytest.mark.parametrize("lift", ["dismiss", "clear_bans"])
def test_dismiss_or_a_project_load_lets_it_try_once_more(lift):
    bg, proc, events, loader = _broken_bg("auto")
    assert bg.tick() == "banned" and len(loader.loads) == 1
    assert set(_ticks(bg, proc, 5)) == {"banned"} and len(loader.loads) == 1
    bg.switch.dismiss()
    getattr(bg, lift)()
    assert _ticks(bg, proc, 1) == ["banned"] and len(loader.loads) == 2       # ONE new attempt
    assert set(_ticks(bg, proc, 10)) == {"banned"} and len(loader.loads) == 2 and loader.infers == 0
    assert _faults(events) == [SAID, SAID]                           # said again for the new attempt only


def test_a_load_that_fails_for_another_reason_says_it_and_what_to_do(monkeypatch):
    import runtime.seamless_switch as ss
    monkeypatch.setattr(ss, "gpu_free_mb", lambda: 312.4, raising=False)   # out of memory: no room, its MB
    bg, proc, events, loader = _broken_bg("suggest", RuntimeError(OOM))
    assert bg.tick() == "banned"
    assert _faults(events) == [SAID_OOM]
    assert bg.status()["last_look"]["outcome"] == (
        "the automatic swipe waits: it could not load l @ 1280 (no room on the GPU: 312 MB free) and will not try it "
        "again until you press Dismiss - set Auto to off")
    assert set(_ticks(bg, proc, 5)) == {"banned"} and len(loader.loads) == 1


@pytest.mark.usefixtures("swipe_auto_apply_released")
def test_after_the_operators_failed_apply_the_held_back_switch_says_it_was_the_apply():
    """The operator's seamless Apply failed (its first frame raised): no ban, but the switch holds the fault, so
    the next definite verdict is a suggestion -- that names the Apply, never « the last automatic switch »."""
    bg, proc, loader, live, port, tap = _setup()
    assert _look(bg, proc, tap, 150) == "run" and bg.last_advice["advice"] == "seen"     # (27/11) nothing shown
    assert bg.request_switch(L1280, "Apply now") is None
    bg.switch.service()
    assert bg.switch.drain()
    _first_frame_raises(port)
    assert bg.switch.fault and bg.status()["auto_banned"] == []
    (_, adv), = _next_runs(bg, proc, tap, 150)
    assert adv["advice"] == "suggest" and adv["why_not_auto"] == APPLY_FAILED
    assert adv["text"] == f"{SAYS} (6.0 ms a frame against 6.0), seen twice: Apply now? ({APPLY_FAILED})"


@pytest.mark.usefixtures("swipe_auto_apply_released")
def test_after_a_failed_automatic_switch_and_a_project_load_it_says_what_lifts_the_fault():
    """A project load lifts the ban, not the fault: the next definite verdict is held back by the fault -- that of
    an automatic switch -- and the operator reads whose it was (the fault says what lifts it)."""
    bg, proc, loader, live, port, tap = _setup()
    _two_looks_ask_the_switch(bg, proc, tap)
    _first_frame_raises(port)
    bg.clear_bans()
    (_, seen), (_, adv) = _next_runs(bg, proc, tap, 300, n=2)
    assert seen["advice"] == "seen"                      # (27/11) the setting was put aside: two looks in a row again
    assert adv["advice"] == "suggest" and adv["why_not_auto"] == AUTO_FAILED
    assert adv["text"] == f"{SAYS} (6.0 ms a frame against 6.0), seen twice: Apply now? ({AUTO_FAILED})"


@pytest.mark.parametrize("lift", ["dismiss", "clear_bans"])
def test_once_the_ban_is_lifted_the_look_no_longer_says_it_waits_for_it(lift):
    """Dismiss (or a project load) lifts the ban: /status stops saying the automatic swipe waits because it could
    not load l @ 1280 at once -- not at the next look that runs (a whole pool of fresh frames first)."""
    bg, proc, events, loader = _broken_bg("auto")
    assert bg.tick() == "banned" and bg.status()["last_look"]["outcome"] == WAITS
    assert _ticks(bg, proc, 2) == ["banned", "banned"]
    bg.switch.dismiss()
    getattr(bg, lift)()
    assert bg.status()["last_look"] is None, "it still says it waits for a setting no longer banned"
    proc.clock += 60.0
    proc.frames = _look_frames(20, t0=proc.clock - 30.0)                 # not a pool: no look runs
    bg.in_run, bg.wall_empty = (lambda: True), (lambda: False)           # (and a dancer on: no load tried now)
    assert bg.tick() == "idle" and len(loader.loads) == 1
    assert bg.status()["last_look"] is None
