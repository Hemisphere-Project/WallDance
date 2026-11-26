"""The settings swipe's referee, collector and verdict (Plan Cordée P5) -- no GPU, no video."""
from dataclasses import replace

import numpy as np

from core.swipe import (Candidate, FrameCollector, FrameScore, Observation, SwipeParams, Tally, bare_groups,
                        decide, on_nobody, score_frame)

X1280 = Candidate("yolo11x-pose", 1280)
X1920 = Candidate("yolo11x-pose", 1280, (1920, 1056))
L1760 = Candidate("yolo11l-pose", 1280, (1760, 960))
L1920 = Candidate("yolo11l-pose", 1280, (1920, 1056))


def _obs(*groups, mask=None):
    return Observation(groups=sorted(groups, key=lambda g: -g[2]), mask=mask, x0=0.0, y0=0.0, ds=4)


def test_labels_engine_names_and_cost():
    assert X1280.label == "x @ 1280" and X1280.engine_file == "yolo11x-pose_1280.engine"
    assert L1920.label == "l @ 1920 x 1056" and L1920.engine_file == "yolo11l-pose_1920x1056.engine"
    # the laptop's order (P2): l 1760 x 960 < l 1920 x 1056 < x 1280 < x 1920 x 1056
    assert L1760.cost < L1920.cost < X1280.cost < X1920.cost


def test_a_group_is_covered_by_a_box_around_its_centre():
    p = SwipeParams()
    obs = _obs((100, 100, 900), (400, 400, 500), (700, 700, 50))      # the last one is too small to be a dancer
    boxes = [(80, 60, 120, 140, 0.4), (600, 600, 640, 660, 0.3), (395, 300, 405, 380, 0.05)]
    s = score_frame(obs, boxes, p)                 # no mask: a box on nobody holds no group centre
    assert (s.groups, s.covered, s.boxes, s.loose) == (2, 1, 2, 1)
    assert bare_groups(obs, boxes, p) == [(400, 400, 500)]


def test_with_a_mask_a_box_is_on_nobody_only_when_no_foreground_lies_under_it():
    p = SwipeParams()
    m = np.zeros((250, 250), np.uint8)
    m[25:50, 20:30] = 1                    # a dancer at x 80-120, y 100-200 ...
    m[50:62, 30:45] = 1                    # ... and her shadow, which pulls the group's centre out of her box
    obs = _obs((125, 190, 4 * 16 * (250 + 180)), mask=m)
    dancer = (80, 100, 120, 200, 0.6)
    ghost = (600, 600, 640, 700, 0.5)      # a figure the empty wall already shows: no foreground
    assert not any(dancer[0] <= g[0] <= dancer[2] and dancer[1] <= g[1] <= dancer[3] for g in obs.groups)
    assert not on_nobody(obs, dancer, p) and on_nobody(obs, ghost, p)
    assert obs.fg_area(dancer) >= 1000 and obs.fg_area(ghost) == 0.0
    s = score_frame(obs, [dancer, ghost], p)
    assert (s.boxes, s.loose) == (2, 1)


def test_the_collector_keeps_the_newest_frames_with_dancers_whatever_the_live_setting_did():
    # an unbiased sample: frames are kept for having a dancer group, spaced, newest first -- lost or not
    p = SwipeParams(frames=3, min_gap_s=0.5)
    c = FrameCollector(p)
    img = np.zeros((4, 4), np.uint8)
    lost, held = _obs((100, 100, 900)), [(80, 60, 120, 140, 0.4)]
    assert not c.offer(0.0, img, None, [])                    # no valid foreground
    assert not c.offer(0.0, img, _obs((5, 5, 20)), [])        # nobody on the wall
    assert c.offer(0.0, img, lost, held)                      # a covered dancer: kept like any other
    assert not c.offer(0.2, img, lost, [])                    # too close to the last kept frame, whatever its kind
    assert c.offer(1.0, img, lost, []) and c.offer(2.0, img, lost, held)
    assert c.ready and c.lost_frames == 1 and [f.lost for f in c.frames] == [False, True, False]
    assert c.offer(4.0, img, lost, [])                        # full: the newest replaces the oldest
    assert [f.t for f in c.frames] == [1.0, 2.0, 4.0]
    # every frame with a dancer counts toward the share, kept or not: lost at 0.2, 1, 4; covered at 0, 2
    assert c.lost_share == 3 / 5


def test_the_collector_drops_frames_older_than_max_age():
    p = SwipeParams(frames=3, covered_frames=2, min_gap_s=0.5, max_age_s=10.0)
    c = FrameCollector(p)
    img = np.zeros((4, 4), np.uint8)
    lost, held = _obs((100, 100, 900)), [(80, 60, 120, 140, 0.4)]
    assert c.offer(0.0, img, lost, []) and c.offer(1.0, img, lost, held) and c.offer(5.0, img, lost, [])
    assert not c.offer(11.5, img, None, [])                   # a newer frame, even a useless one, ages the pools
    assert [f.t for f in c.frames] == [5.0]
    assert c.lost_share == 1.0
    assert c.offer(12.0, img, lost, []) and c.lost_frames == 2
    assert [f.t for f in c.frames_at(16.0)] == [12.0]         # read against the clock, nothing offered since
    c.prune(30.0)
    assert c.frames == [] and not c.ready and c.lost_share is None
    assert c.offer(31.0, img, lost, []) and c.lost_frames == 1
    c.reset()
    assert c.frames == [] and c.lost_share is None


def _tally(groups, covered, loose=0, frames=30):
    t = Tally()
    t.frames, t.groups, t.covered, t.loose = frames, groups, covered, loose
    return t


def test_the_verdict_on_totals_names_a_clear_winner_and_keeps_the_live_setting_otherwise():
    r = decide({X1280: _tally(40, 10), L1920: _tally(40, 36)}, X1280)
    assert r.switch and r.winner == L1920 and r.rows[0].candidate == L1920
    r = decide({X1280: _tally(40, 35), L1920: _tally(40, 36)}, X1280)
    assert not r.switch and r.winner == X1280 and r.reason.startswith("keep x @ 1280")
    r = decide({X1280: _tally(40, 10), L1920: _tally(40, 36, loose=30)}, X1280)
    assert not r.switch and not r.rows[-1].ok and "on nobody" in r.rows[-1].note   # set aside
    r = decide({X1280: _tally(5, 1), L1920: _tally(5, 5)}, X1280)
    assert r.winner is None and not r.switch and r.reason.startswith("no verdict")  # too few dancers to say


def _frames(spec):
    """{candidate: [(groups, covered, loose, lost), ...]} -> tallies over the same frames."""
    out = {}
    for c, rows in spec.items():
        t = Tally()
        for g, cov, lo, lost in rows:
            from core.swipe import FrameScore
            t.add(FrameScore(groups=g, covered=cov, boxes=cov + lo, loose=lo), lost=lost)
        out[c] = t
    return out


def test_the_share_of_lost_frames_is_reported_and_weighs_nothing():
    # frames chosen by the live setting's failures would favour any other setting; on an unbiased sample the plain
    # paired mean is the estimate, whatever ``lost_share`` says
    live = [(2, 1, 0, True)] * 30 + [(2, 2, 0, False)] * 30
    cand = [(2, 2, 0, True)] * 30 + [(2, 1, 0, False)] * 30
    T = _frames({X1280: live, L1920: cand})
    for share in (0.1, 0.5, 0.9, None):
        r = decide(T, X1280, lost_share=share)
        assert not r.switch and r.lost_share == share        # 60 dancers gained, 30 lost... and 30 lost back


def test_a_gain_inside_the_noise_is_not_a_switch_and_more_settings_ask_more():
    # 60 frames of two dancers: the candidate covers one more on 9 frames, one fewer on 2 -- a gain of 5.8 points;
    # its standard error (i.i.d. here, noise_block 1) is about 2.6 points
    live = [(2, 1, 0, True)] * 60
    cand = [(2, 2, 0, True)] * 9 + [(2, 0, 0, True)] * 2 + [(2, 1, 0, True)] * 49
    T = _frames({X1280: live, L1920: cand})
    p = SwipeParams(noise_block=1, cost_gain=0.0)
    row = next(x for x in decide(T, X1280, p).rows if x.candidate == L1920)
    assert abs(row.gain - 7 / 120) < 1e-9 and 0.02 < row.noise < 0.03
    z = row.gain / row.noise
    assert 1.96 < z < 2.58
    assert decide(T, X1280, replace(p, alpha=0.05)).switch                # one alternative: z 1.64 asked
    assert not decide(T, X1280, replace(p, alpha=0.01)).switch            # z 2.33 asked
    # two alternatives at alpha 0.05: each must clear z 1.96 (Bonferroni)
    T[X1920] = _frames({X1920: live})[X1920]
    assert decide(T, X1280, replace(p, alpha=0.05)).switch
    T3 = dict(T)
    T3[L1760] = _frames({L1760: live})[L1760]
    T3[Candidate("yolo11l-pose", 1280)] = _frames({X1280: live})[X1280]
    assert not decide(T3, X1280, replace(p, alpha=0.05)).switch           # four alternatives: z 2.24


def test_among_settings_as_good_as_each_other_the_lighter_one_is_named():
    live = [(2, 1, 0, True)] * 80
    T = _frames({X1280: live, X1920: [(2, 2, 0, True)] * 80,
                 L1920: [(2, 2, 0, True)] * 79 + [(2, 1, 0, True)]})
    r = decide(T, X1280)
    assert r.switch and r.winner == L1920                      # 0.6 points apart: as good, and lighter
    assert "lighter" in next(x for x in r.rows if x.candidate == X1920).note
    T[L1920] = _frames({L1920: [(2, 2, 0, True)] * 74 + [(2, 1, 0, True)] * 6})[L1920]
    r = decide(T, X1280)
    assert r.winner == X1920                                   # 3.75 points apart: the better one


def test_boxes_on_nobody_cost_like_dancers_left_bare():
    live = [(2, 1, 0, True)] * 40
    T = _frames({X1280: live, L1920: [(2, 2, 0, True)] * 40, L1760: [(2, 2, 1, True)] * 40})
    r = decide(T, X1280, SwipeParams(loose_floor=9.0))
    rows = {x.candidate: x for x in r.rows}
    assert r.winner == L1920 and rows[L1760].net < rows[L1920].net


def test_the_operator_reads_plain_sentences():
    T = _frames({X1280: [(2, 1, 0, True)] * 40, L1920: [(2, 2, 0, True)] * 40})
    for r in (decide(T, X1280), decide(_frames({X1280: [(1, 0, 0, True)] * 5}), X1280),
              decide({X1280: Tally()}, X1280)):
        assert r.reason and r.headline() == r.reason
        for word in ("group", "conf", "tally", "None", "nan"):
            assert word not in r.reason
        assert r.reason.isascii() and all(row.note.isascii() for row in r.rows)   # the laptop console is cp1252
    T = _frames({X1280: [(2, 1, 0, True)] * 40, X1920: [(2, 2, 0, True)] * 40, L1920: [(2, 2, 0, True)] * 40,
                 L1760: [(2, 2, 2, True)] * 40})
    r = decide(T, X1280)
    assert r.reason.isascii() and any(row.note for row in r.rows) and all(row.note.isascii() for row in r.rows)
    r = decide(_frames({X1280: [(2, 1, 1, True)] * 40, L1920: [(2, 2, 1, True)] * 40}), X1280)
    assert r.reason.isascii() and "when the wall is empty" in r.reason


def test_the_plate_is_blamed_only_when_every_setting_puts_boxes_on_nobody():
    # one ghost-prone live model on a good plate: the cleaner setting shows the plate is fine -- a verdict
    live = [(2, 1, 1, True)] * 20 + [(2, 1, 0, True)] * 20
    T = _frames({X1280: live, L1920: [(2, 2, 0, True)] * 40})
    r = decide(T, X1280)
    assert r.switch and r.winner == L1920
    # every setting puts boxes where the picture shows nobody: people baked into it, another light -- no verdict,
    # and no order to retake it now (dancers are on the wall)
    T = _frames({X1280: live, L1920: [(2, 2, 1, True)] * 40})
    r = decide(T, X1280)
    assert r.winner is None and not r.switch and "when the wall is empty" in r.reason and r.reason.isascii()
    assert decide(T, X1280, SwipeParams(referee_max_loose=1.0)).winner is not None


def test_a_group_no_setting_covers_is_not_counted_as_a_dancer():
    p = SwipeParams()
    # a dancer and a rope the plate does not know; the live setting misses the dancer, the candidate holds her
    obs = _obs((100, 100, 900), (500, 500, 900))
    T = {X1280: Tally(), L1920: Tally()}
    for _ in range(30):
        T[X1280].add(score_frame(obs, [], p), lost=True)
        T[L1920].add(score_frame(obs, [(80, 60, 120, 140, 0.5)], p), lost=True)
    r = decide(T, X1280, p)
    rows = {x.candidate: x for x in r.rows}
    assert r.groups == 30 and rows[L1920].coverage == 1.0 and rows[X1280].coverage == 0.0
    assert r.switch and "covers 100 % of the dancers" in r.reason


def test_the_image_is_fetched_only_for_a_kept_frame():
    p = SwipeParams(frames=2)
    c = FrameCollector(p)
    calls = []

    def grab():
        calls.append(1)
        return np.zeros((4, 4), np.uint8)
    lost = _obs((100, 100, 900))
    assert c.offer(0.0, grab, lost, []) and len(calls) == 1 and c.frames[0].image.shape == (4, 4)
    assert not c.offer(0.2, grab, lost, []) and len(calls) == 1          # too close: not kept, not fetched
    assert not c.offer(1.0, grab, None, []) and len(calls) == 1          # no valid foreground
    assert c.offer(1.0, grab, lost, [(80, 60, 120, 140, 0.4)]) and len(calls) == 2


def test_the_swipe_names_no_confidence_of_its_own():
    # one threshold serves both models on 08/10: no opinion, and nothing named conf / confidence on the result
    T = _frames({X1280: [(2, 1, 0, True)] * 40, L1920: [(2, 2, 0, True)] * 40})
    r = decide(T, X1280)
    assert r.switch and r.recommended_conf is None
    names = set(vars(r)) | {k for row in r.rows for k in vars(row)}
    assert not {"conf", "confidence"} & names


def test_across_input_sizes_the_better_setting_wins_a_near_tie():
    L1280 = Candidate("yolo11l-pose", 1280)
    live = [(2, 1, 0, True)] * 80
    T = _frames({X1280: live, L1920: [(2, 2, 0, True)] * 80, L1280: [(2, 2, 0, True)] * 79 + [(2, 1, 0, True)]})
    assert L1280.cost < L1920.cost
    assert decide(T, X1280).winner == L1920                         # 0.6 points apart, another input: no tie
    assert decide(T, X1280, SwipeParams(tie_across_inputs=True)).winner == L1280


def test_a_costlier_setting_must_gain_more():
    # x @ 1920 x 1056 costs 1.24 x what x @ 1280 does per frame: its bar is 3 points + 5 x log2(1.24) = 4.5 points
    live = [(2, 1, 0, True)] * 100
    gain4 = [(2, 2, 0, True)] * 8 + [(2, 1, 0, True)] * 92                 # +4 points, far out of the noise
    T = _frames({X1280: live, X1920: gain4})
    r = decide(T, X1280, SwipeParams(noise_block=1))
    row = next(x for x in r.rows if x.candidate == X1920)
    assert abs(row.bar - (0.03 + 0.05 * np.log2(X1920.cost / X1280.cost))) < 1e-9 and not r.switch
    T = _frames({X1280: live, L1920: gain4})                                # cheaper than the live one: 3 points
    assert decide(T, X1280, SwipeParams(noise_block=1)).winner == L1920
    # measured times, when the tallies carry them, replace the model's cost
    t_live, t_x = Tally(), Tally()
    for (g, c, lo, lost), (g2, c2, lo2, lost2) in zip(live, gain4):
        t_live.add(FrameScore(g, c, c + lo, lo), ms=40.0, lost=lost)
        t_x.add(FrameScore(g2, c2, c2 + lo2, lo2), ms=20.0, lost=lost2)
    assert decide({X1280: t_live, X1920: t_x}, X1280, SwipeParams(noise_block=1)).winner == X1920


def test_once_a_setting_is_proven_better_the_best_is_named():
    # the same model at another size errs like the live one: a smaller, sure gain (z 3.9); the other model gains
    # more but errs on its own frames (z 2.5, under the 2.58 two alternatives ask) -- once the first is proven,
    # the better one is named
    live = [(2, 1, 0, True)] * 60
    near = [(2, 2, 0, True)] * 12 + [(2, 1, 0, True)] * 48
    far = [(2, 2, 0, True)] * 24 + [(2, 0, 0, True)] * 10 + [(2, 1, 0, True)] * 26
    T = _frames({X1280: live, X1920: near, L1920: far})
    p = SwipeParams(noise_block=1, cost_gain=0.0, alpha=0.01)
    r = decide(T, X1280, p)
    rows = {x.candidate: x for x in r.rows}
    assert rows[X1920].gain / rows[X1920].noise > 2.58 > rows[L1920].gain / rows[L1920].noise
    assert r.switch and r.winner == L1920
    # nothing proven: keep, even if a point estimate is high
    T = _frames({X1280: live, L1920: far})
    assert not decide(T, X1280, replace(p, alpha=0.001)).switch


def test_a_box_holding_a_share_of_a_merged_group_covers_it():
    # two dancers in contact make one group whose centre lies between them; a box on either covers it
    p = SwipeParams()
    m = np.zeros((250, 250), np.uint8)
    m[25:50, 20:30] = 1                                     # dancer A at x 80-120
    m[25:50, 40:50] = 1                                     # dancer B at x 160-200; the group's centre ~ x 140
    obs = Observation(groups=[(140.0, 150.0, 2 * 16 * 250.0)], mask=m, x0=0.0, y0=0.0, ds=4,
                      group_boxes=[(80.0, 100.0, 200.0, 200.0)])
    on_a = (80, 100, 120, 200, 0.6)
    assert score_frame(obs, [on_a], p).covered == 1
    assert score_frame(obs, [on_a], replace(p, cover_share=0.0)).covered == 0   # the centre test alone
