"""P6 (Plan Cordee): the advice may name a slower model that fits the 15 fps floor; one floor for the looker too.

The owner, 28/11: « Why is the 15% limit ? I would like the suggest propose x (from l) in situations where it brings
substantial improvements [...] even if that costs way more GPU, as long as it fits the 15/20fps budget with TD
running smoothly. » -- « Ok. 15fps budget then. » -- « the stand-down follows your 15 fps floor too ».

1. one floor, ``core.config.SWIPE_AUTO_FRAME_FLOOR_MS`` = 1000 / 15 ms at the live frames' 95th percentile: the
   looker stands down over it (was 55 ms), and the advice's estimate is held against it;
2. the advice (a suggestion shown) may name a model more than 15 % slower when its gain is over the definite bar
   (5 points) on both looks AND the frame the show would have with it stays under the floor: the live frames'
   95th percentile over the 3 s before the look + 1.7 x (the two inference times' difference) -- a faster model,
   or one within the 15 %, keeps the rule it had;
3. an automatic switch keeps the strict rule: never to a model more than 15 % slower, whatever the floor;
4. the line says that cost; over the floor, nothing is shown and the look's row says why;
5. the stand-down's row and log line name the floor.

The figures are the show laptop's beside TouchDesigner (26/11): the frame 35.1 ms at its 95th percentile with
l @ 1920 x 1056 live, l's inference 11.6 ms, x's 21.3.  Each test fails before for the reason its docstring gives
(``advise`` took no frame time: the « slower » rule alone refused every model more than 15 % slower), but those
marked as regression guards, which held already.  CPU, with the fakes of ``test_swipe_auto_live`` and the loop
thread of ``test_swipe_auto_late_frames``.
"""
import ast
import threading
import time
from pathlib import Path

import pytest

from core.swipe import Candidate, SwipeResult, SwipeRow
from core.swipe_auto import MODE_AUTO, MODE_SUGGEST, AutoParams, SwipeAuto
from test_swipe_auto_late_frames import LATE, short  # noqa: F401 - the fixture
from test_swipe_auto_live import _look_frames, _looking_bg
from test_swipe_auto_live import swipe_auto_unlocked  # noqa: F401 - the fixture

TESTS = Path(__file__).resolve().parent
X = Candidate("yolo11x-pose", 1280, (1920, 1056))
L = Candidate("yolo11l-pose", 1280, (1920, 1056))
MS = {L: 11.6, X: 21.3}                      # the two inference times, measured alike (the laptop, 26/11)
FLOOR = "the floor is 15 fps (67 ms)"
LINE = ("x @ 1920 x 1056 covers 97 % of the dancers, l @ 1920 x 1056 88 % (a frame would take about 52 ms against "
        "35; the floor is 15 fps (67 ms)), seen twice: note the time and this line")
# at the floor's edge (67.0 against 66.7, within 1 ms) the estimate and the floor read to one decimal
OVER = ("x @ 1920 x 1056 would see more (97 % against 88 %) but a frame would take about 67.0 ms: over the 15 fps "
        "floor (66.7 ms)")
STRICT = "it is more than 15 % slower (21.3 ms a frame against 11.6)"


def _look(a, now, frame_ms, gain=0.09, ms=None, live=L, winner=X, cov=(0.97, 0.88)):
    """One verdict naming ``winner`` over ``live`` (a gain of ``gain``), on frames no earlier look saw."""
    rows = [SwipeRow(winner, cov[0], 0.0, None, gain=gain), SwipeRow(live, cov[1], 0.0, None)]
    return a.advise(now, SwipeResult(live, winner, True, rows, "said"), newest_frame_t=now + 100.0,
                    ms=MS if ms is None else ms, frame_ms=frame_ms)


# ------------------------------------------------------------------------------------------- 2. the budget rule
def test_on_the_laptops_figures_x_is_advised_from_l_with_its_cost_on_the_line():
    """P = 35.1 ms, l 11.6, x 21.3: the frame with x about 51.6 ms, under the floor -- with a gain of 9 points on
    two looks, x is suggested; the line says the frame and the floor.  Fails before: advise took no frame time."""
    a = SwipeAuto(MODE_SUGGEST)
    first = _look(a, 0.0, 35.1)
    assert first.kind == "seen" and first.text.startswith("x @ 1920 x 1056 covers 97 %")
    adv = _look(a, 160.0, 35.1)
    assert adv.kind == "suggest" and adv.text == LINE, adv.text
    assert adv.too_slow == STRICT and adv.outcome == ""
    from core.swipe_auto import frame_cost
    assert frame_cost(35.1, 21.3, 11.6, AutoParams().cost_factor) == pytest.approx(51.59, abs=0.01)


@pytest.mark.parametrize("p95,shown", [(50.5, False), (49.5, True)])
def test_the_estimate_is_held_against_the_floor(p95, shown):
    """P = 50.5: the frame with x about 67.0 ms, over 66.7 -- refused, the reason said; P = 49.5: about 66.0,
    under it -- suggested.  Fails before: advise took no frame time."""
    a = SwipeAuto(MODE_SUGGEST)
    first = _look(a, 0.0, p95)
    adv = _look(a, 160.0, p95)
    if shown:
        assert first.kind == "seen" and adv.kind == "suggest"
        assert "(a frame would take about 66.0 ms against 49.5; the floor is 15 fps (66.7 ms))" in adv.text, adv.text
    else:
        for x in (first, adv):
            assert x.kind == "none" and x.why_not_auto == OVER and x.outcome == OVER, x
        assert a.keep_wait() > 0, "a look the floor refused does not back off like a « keep »"


@pytest.mark.parametrize("p95,said", [
    (49.2, "(a frame would take about 65.7 ms against 49.2; the floor is 15 fps (66.7 ms))"),   # 0.98 ms under
    (49.0, "(a frame would take about 65 ms against 49; the floor is 15 fps (67 ms))"),         # 1.18 ms under
])
def test_under_the_floor_within_1_ms_of_it_the_line_reads_tenths(p95, said):
    """Allowed, at the edge: the frame with x about 65.7 ms, within 1 ms under the floor -- the estimate, the frame
    before the look and the floor to one decimal; 1.2 ms under, whole milliseconds as before.  Fails before: always
    whole milliseconds (« about 66 ms against 49; the floor is 15 fps (67 ms) »)."""
    a = SwipeAuto(MODE_SUGGEST)
    _look(a, 0.0, p95)
    adv = _look(a, 160.0, p95)
    assert adv.kind == "suggest" and said in adv.text, adv.text


@pytest.mark.parametrize("p95,said", [
    (51.0, "but a frame would take about 67.5 ms: over the 15 fps floor (66.7 ms)"),          # 0.82 ms over
    (51.6, "but a frame would take about 68 ms: over the 15 fps floor (67 ms)"),              # 1.42 ms over
])
def test_over_the_floor_within_1_ms_of_it_the_row_reads_tenths(p95, said):
    """Refused, at the edge: the frame with x about 67.5 ms, within 1 ms over the floor -- the estimate and the floor
    to one decimal, never the same number on both sides of « over »; 1.4 ms over, whole milliseconds as before.
    Fails before: « about 67 ms: over the 15 fps floor (67 ms) »."""
    a = SwipeAuto(MODE_SUGGEST)
    adv = _look(a, 0.0, p95)
    assert adv.kind == "none" and adv.outcome.endswith(said) and adv.why_not_auto == adv.outcome, adv.outcome


def test_over_the_floor_ends_the_streak():
    """Seen once under the floor, then over it: the streak is over -- the next look under the floor is « seen »
    again, not a suggestion on two looks that are not in a row.  Fails before: advise took no frame time."""
    a = SwipeAuto(MODE_SUGGEST)
    assert _look(a, 0.0, 35.1).kind == "seen"
    assert _look(a, 160.0, 50.5).kind == "none"
    assert _look(a, 320.0, 35.1).kind == "seen"
    assert _look(a, 480.0, 35.1).kind == "suggest"


@pytest.mark.parametrize("gains", [(0.04, 0.09), (0.09, 0.04)])
def test_a_slower_model_with_4_points_on_one_of_the_two_looks_is_not_advised(gains):
    """A gain under the 5-point bar on either look: the strict rule's refusal, as before -- whatever the floor.
    Fails before: advise took no frame time."""
    a = SwipeAuto(MODE_SUGGEST)
    advs = [_look(a, 0.0, 35.1, gain=gains[0]), _look(a, 160.0, 35.1, gain=gains[1])]
    assert all(x.kind != "suggest" for x in advs), advs
    weak = advs[gains.index(0.04)]
    assert weak.kind == "none" and weak.why_not_auto == STRICT


@pytest.mark.parametrize("frame_ms,ms,why", [
    (None, MS, STRICT),                                         # P not measured
    (35.1, {X: 21.3}, "its time or the live model's is not known"),
    (35.1, {L: 11.6}, "its time or the live model's is not known"),
])
def test_a_missing_frame_time_or_inference_time_refuses_as_before(frame_ms, ms, why):
    """No P, or either inference time unknown: refused, the reason the strict rule gave.  Fails before: advise took
    no frame time."""
    a = SwipeAuto(MODE_SUGGEST)
    for now in (0.0, 160.0):
        adv = _look(a, now, frame_ms, ms=ms)
        assert adv.kind == "none" and adv.why_not_auto == why and adv.outcome == "", adv


@pytest.mark.parametrize("ms,gain,said", [
    ({L: 11.6, X: 21.3}, 0.09, "(11.6 ms a frame against 21.3)"),     # l from x: faster
    ({L: 6.84, X: 6.0}, 0.03, "(6.8 ms a frame against 6.0)"),       # 14 % slower, a gain of 3 points: within
])
def test_a_faster_model_or_one_within_15_percent_keeps_its_rule_and_its_line(ms, gain, said):
    """From x, l is faster (or 14 % slower, within the tolerance, a marginal gain): the frame time changes nothing --
    the advice is the one given without it, the line with the two inference times.  Fails before: advise took no
    frame time (the same advice without it held: ``test_swipe_auto_speed``)."""
    with_p, without = SwipeAuto(MODE_SUGGEST), SwipeAuto(MODE_SUGGEST)
    for now in (0.0, 160.0):
        a = _look(with_p, now, 69.0, gain=gain, ms=ms, live=X, winner=L)    # even over the floor: not read
        b = _look(without, now, None, gain=gain, ms=ms, live=X, winner=L)
        assert a == b
    assert a.kind == "suggest" and a.too_slow == "" and said in a.text
    assert a.text == (f"l @ 1920 x 1056 covers 97 % of the dancers, x @ 1920 x 1056 88 % {said}, seen twice: "
                      "note the time and this line")


# ------------------------------------------------------------------------------------- 3. the switch: strict
def test_mode_auto_never_switches_to_a_slower_model_the_floor_allows():
    """Mode auto, x slower than l and well under the floor, a gain of 9 points look after look: shown as advice,
    the reason it is not switched to said, never « apply »; l from x (faster) still switches.  Fails before: advise
    took no frame time."""
    a = SwipeAuto(MODE_AUTO)
    kinds = [_look(a, t, 35.1).kind for t in (0.0, 160.0, 320.0, 480.0, 640.0)]
    assert kinds == ["seen", "suggest", "suggest", "suggest", "suggest"], kinds
    adv = _look(a, 800.0, 35.1)
    assert adv.why_not_auto == f"{STRICT}: the app never switches to it by itself" and adv.held == ""
    b = SwipeAuto(MODE_AUTO)
    assert [_look(b, t, 35.1, live=X, winner=L).kind for t in (0.0, 160.0)] == ["seen", "apply"]


# ------------------------------------------------------------------------------------------ the looker, whole
class _Show:
    """The live loop on its own thread: a frame every 4 ms, its own time ``total()``, its network pass ``yolo``
    ms -- the time the looker samples for the live model."""

    def __init__(self, proc, total, yolo=11.6, period=0.004):
        self.proc, self.total, self.yolo, self.period = proc, total, yolo, period
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            self.proc.timing = {"total": float(self.total()), "yolo": float(self.yolo)}   # a new dict per frame
            self.proc.frame_done_t = time.perf_counter()
            self.proc.frame_done.set()
            time.sleep(self.period)

    def end(self):
        self.stop.set()
        self.thread.join(1.0)


def _slow_other(loader, ms=21.3):
    """The other model's inferences take ``ms`` each (its own time, as the run measures it)."""
    real = loader.infer

    def infer(*a, **k):
        run = real(*a, **k)
        run.ms[:] = [ms] * len(run.ms)
        return run
    loader.infer = infer


# the fakes' scene: the other model (l @ 1280) sees the dancer on every frame, the live one (x @ 1280) on 50 of 150
BG_COST = "(a frame would take about 52 ms against 35; the floor is 15 fps (67 ms))"
BG_LINE = f"l @ 1280 covers 100 % of the dancers, x @ 1280 33 % {BG_COST}, seen twice: note the time and this line"
BG_OVER = ("l @ 1280 would see more (100 % against 33 %) but a frame would take about 67.0 ms: over the 15 fps "
           "floor (66.7 ms)")


def test_the_looker_advises_a_slower_model_on_the_frames_before_the_look(short):
    """The live frames take 35.1 ms before each look and 52 ms during it (its inferences beside them; under the
    floor: it runs on): the estimate is made on the 35.1 -- « against 35 », about 52 ms, suggested.  On the 52 it
    would be about 68 ms, over the floor.  Fails before: refused, « more than 15 % slower »."""
    bg, proc, events, loader, live = _looking_bg()
    _slow_other(loader)
    proc.frames = _look_frames()
    show = _Show(proc, lambda: 52.0 if bg.running else 35.1)
    try:
        assert bg.tick() == "run" and bg.last_advice["advice"] == "seen", bg.last_advice
        assert bg.last_look["ms_frame"] == 35.1 and bg.last_look["ms_cand"] == 21.3
        proc.frames = _look_frames(150, t0=150.0)
        assert bg.tick() == "run"
        assert bg.suggestion is not None and bg.suggestion["text"] == BG_LINE, bg.suggestion
        assert [p["text"] for k, p in events if k == "suggest"] == [BG_LINE]
    finally:
        show.end()


def test_over_the_floor_the_row_says_why_and_a_suggestion_on_screen_is_not_confirmed(short):
    """Shown on two looks at 35.1 ms; the next look with the live frames at 50.5 ms (about 67.0 ms with it): no
    toast, the look's row and the advice's reason say why, and the suggestion on screen is not confirmed (its line
    stops inviting).  A first look over the floor shows nothing.  Fails before: never shown (« more than 15 %
    slower »)."""
    bg, proc, events, loader, live = _looking_bg()
    _slow_other(loader)
    p95 = [35.1]
    proc.frames = _look_frames()
    show = _Show(proc, lambda: p95[0])
    try:
        bg.tick()
        proc.frames = _look_frames(150, t0=150.0)
        bg.tick()
        assert bg.suggestion is not None and bg.suggestion["text"] == BG_LINE
        p95[0] = 50.5
        proc.frames = _look_frames(150, t0=300.0)
        proc.clock += 301.0                                    # past the back-off after the suggestion
        assert bg.tick() == "run"
        assert bg.status()["last_look"]["outcome"] == BG_OVER
        assert bg.last_advice["advice"] == "none" and bg.last_advice["why_not_auto"] == BG_OVER
        assert bg.suggestion is not None and bg.suggestion["unconfirmed"] == 1
        assert "not confirmed at the last look" in bg.suggestion["text"] and BG_OVER in bg.suggestion["text"]
        assert len([1 for k, p in events if k == "suggest" and not p.get("again")]) == 1     # one toast, the first
    finally:
        show.end()
    bg, proc, events, loader, live = _looking_bg()
    _slow_other(loader)
    proc.frames = _look_frames()
    show = _Show(proc, lambda: 50.5)
    try:
        assert bg.tick() == "run" and bg.status()["last_look"]["outcome"] == BG_OVER
        assert bg.suggestion is None and [k for k, p in events if k == "suggest"] == []
        assert bg.auto.policy.keep_wait() > 0                  # backed off as after a « keep »
    finally:
        show.end()


def test_mode_auto_shows_the_slower_model_as_advice_and_never_switches_to_it(short, swipe_auto_unlocked):
    """Mode auto released (the fixture), the slower model under the floor, three looks: a suggestion with the
    reason it is not switched to, no switch asked, no « switching » toast.  Fails before: refused, never shown."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    _slow_other(loader)
    proc.frames = _look_frames()
    show = _Show(proc, lambda: 35.1)
    try:
        assert bg.tick() == "run" and bg.last_advice["advice"] == "seen", bg.last_advice
        for k in (1, 2):
            proc.frames = _look_frames(150, t0=150.0 * k)
            proc.clock += 301.0 if k == 2 else 0.0             # (past the back-off after the suggestion)
            assert bg.tick() == "run"
        assert bg.suggestion is not None and bg.suggestion["text"].startswith("l @ 1280 covers 100 %")
        assert bg.last_advice["advice"] == "suggest"
        assert bg.last_advice["why_not_auto"] == ("it is more than 15 % slower (21.3 ms a frame against 11.6): "
                                                  "the app never switches to it by itself")
        assert bg.switch._want is None and [k for k, p in events if k == "switching"] == []
        assert bg.suggestion["seen"] == 3
    finally:
        show.end()


def test_regression_guard_a_faster_model_keeps_its_line_whatever_the_frames(short):
    """Regression guard (held before): the other model faster (5.0 ms against the live 11.6), the live frames at
    50 ms: the suggestion's line gives the two inference times, as before."""
    bg, proc, events, loader, live = _looking_bg()
    _slow_other(loader, ms=5.0)
    proc.frames = _look_frames()
    show = _Show(proc, lambda: 50.0)
    try:
        bg.tick()
        proc.frames = _look_frames(150, t0=150.0)
        bg.tick()
        assert bg.suggestion["text"] == ("l @ 1280 covers 100 % of the dancers, x @ 1280 33 % (5.0 ms a frame "
                                         "against 11.6), seen twice: note the time and this line")
    finally:
        show.end()


# ---------------------------------------------------------------------------- 1 and 5. the looker's floor
def test_the_looker_runs_at_60_ms_and_stands_down_at_70_naming_the_floor(short, capsys):
    """The live frames at a 95th percentile of 60 ms: a look starts and runs whole (it stood down at 55 before);
    at 70 ms: no look, the row and the log line name the floor.  Fails before: 60 stood down; the words said a
    55 ms budget."""
    bg, proc, events, loader, live = _looking_bg()
    proc.frames = _look_frames()
    show = _Show(proc, lambda: 60.0, yolo=12.0)
    try:
        assert bg.tick() == "run" and loader.infers == 150, bg.status()["last_look"]
    finally:
        show.end()
    bg, proc, events, loader, live = _looking_bg()
    proc.frames = _look_frames()
    show = _Show(proc, lambda: 70.0, yolo=12.0)
    try:
        assert bg.tick() == "waiting" and loader.infers == 0
        row = bg.status()["last_look"]["outcome"]
        assert row == LATE.format(70) == ("the automatic swipe waits: the live frames run late (one in twenty takes "
                                          "70 ms or more; the floor is 15 fps (67 ms))")
        out = capsys.readouterr().out
        assert ("[SwipeAuto] the live frames run late (one in twenty takes 70 ms or more, the floor is 15 fps "
                "(67 ms)): no look for 300 s") in out, out
        assert "budget" not in out
    finally:
        show.end()


def test_the_frames_95th_percentile_is_kept_under_the_floor_too():
    """``LiveFrames.p95``: the measure the advice reads, under the budget as over it; ``late`` stays the stand-down's
    (None under it).  Fails before: no such measure."""
    from runtime.swipe_background import LiveFrames
    lf = LiveFrames()
    now = time.perf_counter()
    lf.rows.extend((now, 35.1) for _ in range(40))
    assert lf.p95(now - 1.0) == 35.1 and lf.late(now - 1.0, 1000.0 / 15) is None
    assert LiveFrames().p95(now - 1.0) is None


def test_one_floor_in_config_and_the_drivers():
    """The floor is 1000 / 15 ms, the old 55 ms budget gone; the factor 1.7; the replay driver's default and help
    name the floor, the policy's simulator can give the advice a frame time.  Fails before: SWIPE_AUTO_FRAME_BUDGET_MS
    = 55."""
    import core.config as C
    assert C.SWIPE_AUTO_FRAME_FLOOR_MS == pytest.approx(1000.0 / 15) and C.SWIPE_ADVICE_COST_FACTOR == 1.7
    assert not hasattr(C, "SWIPE_AUTO_FRAME_BUDGET_MS")
    p = AutoParams()
    assert p.floor_ms == C.SWIPE_AUTO_FRAME_FLOOR_MS and p.cost_factor == 1.7 and p.slower == 1.15
    assert p.definite_gain == 0.05                                      # the bar itself unchanged
    src = (TESTS / "switch_replay.py").read_text(encoding="utf-8")
    assert "SWIPE_AUTO_FRAME_FLOOR_MS" in src and "SWIPE_AUTO_FRAME_BUDGET_MS" not in src
    tree = ast.parse((TESTS / "swipe_auto_gate.py").read_text(encoding="utf-8"))
    opts = [c.args[0].value for c in ast.walk(tree) if isinstance(c, ast.Call)
            and getattr(c.func, "attr", "") == "add_argument" and c.args and isinstance(c.args[0], ast.Constant)]
    assert "--frame-ms" in opts
