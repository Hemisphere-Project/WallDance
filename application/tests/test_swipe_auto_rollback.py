"""P6 (Plan Cordee): a switch that was rolled back leaves the automatic swipe as it was, plus the ban.

The seamless switch swaps the model between two frames and keeps the old one ready for 3 frames (the probation): a
first frame that raises puts it back.  The policy hears of a switch only once those frames ran well: a switch rolled
back -- automatic or the operator's Apply -- starts no dwell, no « setting just left », keeps the streak, the memory
of the frames already judged and the keep counters; no look starts because of it and none judges again the frames an
earlier look judged.  The next look comes at the cadence it would have had without the switch and, for an automatic
switch, says the ban at once.  A switch that succeeds starts the dwell and the pool as before.  (Measured on the GPU
in k4_autofail_s7: a look 3 s after the rollback, on the 150 frames the look before had judged.)  CPU, with the fakes
of ``test_swipe_auto_live``."""
import numpy as np

from core.swipe import Observation, SwipeFrame
from test_seamless_switch import L1280, X1280, _live, _Port
from test_swipe_auto_live import DANCER, ON_HER, _bg, _LookRunner
import pytest  # noqa: E402
from test_swipe_auto_live import swipe_auto_unlocked  # noqa: E402,F401 - the fixture

# mode auto set on the BackgroundSwipe itself, past the app's doors: the release list must name it too (the
# last guard before a switch, since 27/11); the build ships locked
pytestmark = pytest.mark.usefixtures("swipe_auto_unlocked")

BAN = "the automatic swipe will not try l @ 1280 again until you press Dismiss"
DWELL = "the setting changed a moment ago"


class _Tap:
    """The frame tap: kept frames at ``times`` (a distinct image each, so the frames a run judged can be named), the
    newest 150 kept before ``now`` in memory; the live setting left the dancer bare on 100 of any 150 in a row."""

    def __init__(self, times):
        self.frames = [SwipeFrame(float(t), np.zeros((64, 48), np.uint8), Observation(groups=[DANCER]),
                                  i % 150 < 100, () if i % 150 < 100 else (ON_HER,)) for i, t in enumerate(times)]
        self.t_of = {id(f.image): f.t for f in self.frames}

    def kept(self, now, after=None):
        fr = [f for f in self.frames if f.t < now and (after is None or f.t > after)]
        return fr[-150:]


class _JudgingRunner(_LookRunner):
    """... and remembers the images of every run."""

    def __init__(self):
        super().__init__()
        self.judged = []

    def infer(self, images, cand, runner, **kw):
        self.judged.append([id(im) for im in images])
        return super().infer(images, cand, runner, **kw)


def _setup(mode="auto"):
    live = {"v": _live()}
    loader, port = _JudgingRunner(), _Port()
    bg, proc, events = _bg(live, loader=loader, port=port, mode=mode)
    bg.pace_s = 0.0
    proc.swipe_lost_share = lambda: 0.66
    return bg, proc, loader, live, port, _Tap(range(800))


def _look(bg, proc, tap, now, after=None):
    proc.clock = float(now)
    proc.frames = tap.kept(now, after)
    return bg.tick()


def _next_runs(bg, proc, tap, start, n=1, stop=1000, after=None):
    """The loop clock of the next ``n`` looks that ran, a look a second from ``start``, and their advice."""
    out = []
    for now in range(int(start) + 1, stop):
        if _look(bg, proc, tap, now, after) == "run":
            out.append((now, dict(bg.last_advice)))
            if len(out) == n:
                break
    return out


def _two_looks_ask_the_switch(bg, proc, tap):
    """Look 1 (frames 0..149) names l @ 1280 once, look 2 (150..299) twice: mode auto asks the switch."""
    assert _look(bg, proc, tap, 150) == "run" and bg.last_advice["why_not_auto"] == "seen once, it looks again"
    assert _look(bg, proc, tap, 300) == "run" and bg.last_advice["advice"] == "apply"
    assert bg.switch._want is not None and bg.switch._want["auto"] is True
    before = dict(vars(bg.auto.policy))
    bg.switch.service()
    assert bg.switch.drain()                             # swapped at 300 s, on probation
    return before


def _first_frame_raises(port):
    port.hooks[0](RuntimeError("the engine raised on its first frame"), None)


def test_a_rolled_back_switch_starts_no_look_before_the_cadence():
    """(a) The policy is as it was before the swap, and the next look comes when it would have come without the
    switch (a pool of 150 frames kept after look 2), never right after the rollback."""
    twin, tproc, _, _, _, ttap = _setup(mode="suggest")       # the same looks, no switch
    assert _look(twin, tproc, ttap, 150) == "run" and _look(twin, tproc, ttap, 300) == "run"
    (without, _), = _next_runs(twin, tproc, ttap, 300)
    bg, proc, loader, live, port, tap = _setup()
    before = _two_looks_ask_the_switch(bg, proc, tap)
    _first_frame_raises(port)
    assert bg.switch.fault and bg.status()["auto_banned"] == [L1280.engine_file]
    after = dict(vars(bg.auto.policy))
    loads, infers = len(loader.loads), loader.infers
    bg.dismiss()                                         # the ban stands the looker down: Dismiss lifts it
    (first, _), = _next_runs(bg, proc, tap, 300)
    assert first == without == 600, f"a look started by the failure itself, at {first} s (without the switch: " \
                                    f"{without} s)"
    assert len(loader.loads) == loads + 1 and loader.infers == infers + 150     # that look's own load and run only
    assert after == before, "the rolled-back switch changed the policy"


def test_a_rolled_back_switch_never_judges_again_the_frames_a_look_judged():
    """(b) The look after the rollback runs on frames kept after look 2's newest, none of look 2's."""
    bg, proc, loader, live, port, tap = _setup()
    _two_looks_ask_the_switch(bg, proc, tap)
    look2 = {tap.t_of[i] for i in loader.judged[-1]}
    assert max(look2) == 299.0
    _first_frame_raises(port)
    bg.dismiss()                                         # the ban stands the looker down: Dismiss lifts it
    _next_runs(bg, proc, tap, 300)
    again = sorted(look2 & {tap.t_of[i] for i in loader.judged[-1]})
    assert not again, f"{len(again)} frames judged again ({again[0]:.0f} .. {again[-1]:.0f} s)"


def test_the_looks_after_a_failed_automatic_switch_say_the_ban_never_the_dwell():
    """(c) The looker stands down for the banned setting: no look runs, the look's row says the ban, at once and
    after, never « the setting changed a moment ago » (the setting did not change); nothing is suggested."""
    bg, proc, loader, live, port, tap = _setup()
    _two_looks_ask_the_switch(bg, proc, tap)
    _first_frame_raises(port)
    assert _next_runs(bg, proc, tap, 300, n=2) == [] and bg.switch._want is None
    assert bg.status()["last_look"]["outcome"] == BAN and bg.status()["suggestion"] is None


def test_an_operators_failed_apply_leaves_the_policy_as_it_was():
    """(d) Mode auto, look 1 suggests l @ 1280 (seen once); the operator's Apply swaps it in, its first frame
    raises: no ban (it was not ours), and the policy, the pool and the cadence are as before the Apply."""
    bg, proc, loader, live, port, tap = _setup()
    assert _look(bg, proc, tap, 150) == "run" and bg.last_advice["advice"] == "seen"     # (27/11) nothing shown
    before = dict(vars(bg.auto.policy))
    assert bg.request_switch(L1280, "Apply now") is None
    bg.switch.service()
    assert bg.switch.drain()
    _first_frame_raises(port)
    assert bg.switch.fault and bg.status()["auto_banned"] == []
    assert dict(vars(bg.auto.policy)) == before, "the rolled-back Apply changed the policy"
    (first, adv), = _next_runs(bg, proc, tap, 150)
    assert first == 300 and adv["streak"] == 2              # 150 frames after look 1's newest; the streak went on


def test_a_look_during_the_probation_does_not_turn_the_rollback_into_a_change_by_hand():
    """The looker may look while the switched-in model runs its first frames (it sees the new setting, the switch
    busy); after the rollback the old setting is live again -- not a change by hand, no dwell."""
    bg, proc, loader, live, port, tap = _setup()
    before = _two_looks_ask_the_switch(bg, proc, tap)
    live["v"] = _live(cand=L1280, model_id=2)            # what the app reads during the probation
    assert _look(bg, proc, tap, 300) == "busy"
    _first_frame_raises(port)
    live["v"] = _live()                                  # the old model took the frame back
    did = _look(bg, proc, tap, 301)
    assert "manual-change" not in [h["kind"] for h in bg.history], "the rollback was taken for a change by hand"
    assert did == "banned" and dict(vars(bg.auto.policy)) == before        # (the ban: no look for l @ 1280)


def test_a_switch_that_succeeds_starts_the_dwell_and_the_pool_as_before():
    """(e) No regression: once the switched-in model ran its first frames the dwell runs from the swap, the streak
    starts over and every frame kept after the swap is fresh (the tap starts its pool again with the new model): the
    first look there says « seen 1 time(s) », the second « the setting changed a moment ago »."""
    bg, proc, loader, live, port, tap = _setup()
    _two_looks_ask_the_switch(bg, proc, tap)
    live["v"] = _live(cand=L1280, model_id=2)
    port.hooks[1]()                                      # its first frames ran well
    pol = bg.auto.policy
    assert (pol._switched_at, pol._left, pol._streak, pol._seen_until) == (300.0, X1280, 0, None)
    assert bg.switch.fault is None and bg.resident.candidate == X1280
    dense = _Tap([300.0 + 0.5 * j for j in range(1, 700)])     # two pools inside the dwell
    runs = _next_runs(bg, proc, dense, 300, n=2, after=300.0)
    assert [a["why_not_auto"] for _, a in runs] == ["seen once, it looks again", DWELL]
    assert runs[-1][0] - 300 < bg.auto.params.dwell_s
    assert "manual-change" not in [h["kind"] for h in bg.history]
