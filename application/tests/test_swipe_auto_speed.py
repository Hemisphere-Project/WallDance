"""P6 (Plan Cordee): the automatic swipe's « slower » rule compares like with like.

The candidate is timed in the background run -- beside the live loop, on the same GPU and interpreter, without a
CUDA graph -- and the live model in the loop: their medians are not alike (mgr-ran 26/11: l @ 1280 read 18.8-24.4 ms
in the background, 13.2-13.9 once live; x @ 1280 live 16.2-17.1).  The rule compares the same low percentile of
both over the run (``core.swipe_auto.alike_ms``): a candidate faster once live is not called slower, a slower one
still is.  The referee's cost bar is not fed by those times in the background run (the live row comes from stored
boxes, without times: ``Candidate.cost`` for every row).  CPU, with the fakes of ``test_swipe_auto_live``."""
import random

import pytest

from core.swipe import SwipeParams
from core.swipe_auto import AutoParams, alike_ms, bar_for
from test_seamless_switch import L1280, X1280, _live
from test_swipe_auto_live import _look_frames, _looking_bg


def _times(seed, n, floor, spread, contended, share_free=0.15):
    """``n`` inference times: ``share_free`` of them with nobody in the way (``floor`` .. ``floor + spread``), the
    rest contended (``contended`` = (low, high))."""
    rnd = random.Random(seed)
    free = int(round(n * share_free))
    out = [rnd.uniform(floor, floor + spread) for _ in range(free)]
    out += [rnd.uniform(*contended) for _ in range(n - free)]
    rnd.shuffle(out)
    return out


def _timed(bg, proc, loader, cand_times, live_times):
    """The candidate's inferences take ``cand_times``; the live loop's network pass before each one ``live_times``
    (what the run samples after each pace)."""
    real = loader.infer

    def infer(images, cand, runner, **kw):
        i = [0]
        pace = kw.get("pace")

        def paced():
            proc.timing = {"yolo": live_times[i[0] % len(live_times)]}
            i[0] += 1
            if pace is not None:
                pace()
        run = real(images, cand, runner, **{**kw, "pace": paced})
        run.ms[:] = [cand_times[k % len(cand_times)] for k in range(len(run.ms))]
        return run
    loader.infer = infer


def test_alike_ms_takes_the_same_low_percentile_of_both():
    assert alike_ms([], []) == (None, None)
    assert alike_ms([10.0] * 5, []) == (10.0, None)
    c, l = alike_ms(list(range(1, 101)), [x / 2.0 for x in range(1, 101)], q=10.0)
    assert c == pytest.approx(10.9) and l == pytest.approx(5.45)
    assert alike_ms([30.0, None, 0.0, 12.0], [16.0], q=0.0) == (12.0, 16.0)    # no time, no zero
    assert AutoParams().speed_q == 10.0 and AutoParams().slower == 1.15        # the rule itself unchanged


def test_a_candidate_faster_once_live_is_not_called_slower_by_its_contended_median():
    """e_auto_s7 run 2 (mgr-ran, 26/11): l @ 1280's background median 24.4 ms against x @ 1280's live 16.9 -- the
    rule held the switch ("it is slower (24 ms a frame against 17)") although l runs 13.2-13.9 live.  Its
    uncontended inferences read like its live time: the second definite verdict switches."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    cand = _times(1, 150, 13.0, 1.5, (20.0, 30.0))
    live_t = _times(2, 150, 15.8, 0.6, (16.0, 18.6), share_free=0.12)
    assert sorted(cand)[75] > 1.15 * sorted(live_t)[75]                       # what the old rule read: slower
    _timed(bg, proc, loader, cand, live_t)
    proc.frames = _look_frames(150)
    assert bg.tick() == "run" and bg.last_advice["advice"] == "suggest"       # seen once
    proc.frames = _look_frames(150, t0=150.0)
    assert bg.tick() == "run"
    assert bg.last_advice["advice"] == "apply" and bg.switch._want is not None
    look = bg.last_look
    assert look["ms_cand"] < 15.0 and 15.0 < look["ms_live"] < 16.5 and look["ms_p50"] > 20.0


def test_a_slower_candidate_is_still_held_and_the_sentence_shows_what_was_compared():
    """x @ 1280 beside l @ 1280 live: x is about 1.25 times slower at the same input -- still « slower », and the
    sentence gives the two numbers the rule compared."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    live["v"] = _live(cand=L1280, model_id=2)
    bg._last_live = L1280                                                     # not a change by hand
    cand = _times(3, 150, 17.0, 1.5, (24.0, 40.0))
    live_t = _times(4, 150, 13.2, 0.5, (13.5, 15.0), share_free=0.12)
    _timed(bg, proc, loader, cand, live_t)
    proc.frames = _look_frames(150)
    bg.tick()
    proc.frames = _look_frames(150, t0=150.0)
    assert bg.tick() == "run"
    adv, look = bg.last_advice, bg.last_look
    assert adv["advice"] == "suggest" and bg.switch._want is None
    assert look["ms_cand"] > 1.15 * look["ms_live"]
    said = f"it is slower ({look['ms_cand']:.0f} ms a frame against {look['ms_live']:.0f})"
    assert adv["why_not_auto"] == said and adv["text"].endswith(f"Apply now ({said})")


@pytest.mark.parametrize("live_c,cand_c", [(X1280, L1280), (L1280, X1280)])
def test_the_referees_bar_in_the_background_is_the_models_cost_not_their_times(live_c, cand_c):
    """The live row is scored from the boxes the live pass stored (no times), so ``decide``'s cost bar is
    ``Candidate.cost``'s for every row, whatever the candidate's background times read."""
    for ms in (6.0, 60.0):
        bg, proc, events, loader, live = _looking_bg(mode="suggest")
        if live_c != X1280:
            live["v"] = _live(cand=live_c, model_id=2)
        bg._last_live = live_c
        _timed(bg, proc, loader, [ms], [15.0])
        proc.frames = _look_frames(150)
        assert bg.tick() == "run"
        rows = {r.candidate: r for r in bg.suggestion["result"].rows}
        sp = bg._run_params(proc)
        assert rows[cand_c].bar == pytest.approx(bar_for(live_c, cand_c, sp))
        assert rows[live_c].ms_p50 is None and rows[cand_c].ms_p50 == pytest.approx(ms)
    assert bar_for(X1280, L1280, SwipeParams()) == pytest.approx(0.03)
    assert bar_for(L1280, X1280, SwipeParams()) == pytest.approx(0.03 + 0.05 * 0.6215, abs=1e-3)
