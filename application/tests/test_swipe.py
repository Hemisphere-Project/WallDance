"""The settings swipe's referee, collector and verdict (Plan Cordée P5) -- no GPU, no video."""
import numpy as np

from core.swipe import (Candidate, FrameCollector, Observation, SwipeParams, Tally, bare_groups, decide,
                        on_nobody, score_frame)

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


def test_the_collector_keeps_lost_frames_spaced_and_counts_how_often_the_live_setting_loses_one():
    p = SwipeParams(frames=3, covered_frames=2, min_gap_s=0.5)
    c = FrameCollector(p)
    img = np.zeros((4, 4), np.uint8)
    lost, held = _obs((100, 100, 900)), [(80, 60, 120, 140, 0.4)]
    assert not c.offer(0.0, img, None, [])                    # no valid foreground
    assert not c.offer(0.0, img, _obs((5, 5, 20)), [])        # nobody on the wall
    assert c.offer(0.0, img, lost, held)                      # a covered dancer
    assert not c.offer(0.2, img, lost, held)                  # too close to the last covered frame
    assert c.offer(0.2, img, lost, [])                        # a lost frame: spaced from the last LOST frame only
    assert not c.offer(0.4, img, lost, [])
    for t in (1.0, 2.0):
        assert c.offer(t, img, lost, [])
    assert c.ready and c.lost_frames == 3
    assert c.offer(4.0, img, lost, [])                        # full: the newest replaces the oldest
    assert [f.t for f in c.frames if f.lost] == [1.0, 2.0, 4.0]
    for t in (5.0, 6.0, 7.0):
        assert c.offer(t, img, lost, held)                    # covered frames: the newest two are kept
    assert [f.lost for f in c.frames] == [True, True, True, False, False]
    assert [f.t for f in c.frames][-2:] == [6.0, 7.0]
    # every frame with a dancer counts, kept or not: 5 lost (0.2 0.4 1 2 4), 5 covered (0 0.2 5 6 7)
    assert c.lost_share == 0.5


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


def test_lost_frames_are_weighed_by_how_often_they_happen():
    # on lost frames the candidate covers what the live setting misses; on covered frames it loses a dancer
    # the live setting holds, one frame in three
    live = [(2, 1, 0, True)] * 30 + [(2, 2, 0, False)] * 15
    cand = [(2, 2, 0, True)] * 30 + [(2, 1, 0, False)] * 5 + [(2, 2, 0, False)] * 10
    T = _frames({X1280: live, L1920: cand})
    r = decide(T, X1280, lost_share=0.6)             # the live setting often loses a dancer: worth it
    assert r.switch and r.winner == L1920
    r = decide(T, X1280, lost_share=0.1)             # it rarely does: the candidate would lose more than it gains
    assert not r.switch and r.winner == X1280
    assert decide(T, X1280).lost_share == 30 / 45    # no share given: the kept frames' own mix


def test_a_gain_inside_the_noise_is_not_a_switch():
    # 30 frames of two dancers: the candidate covers one more on 5 frames, one fewer on 1 -- a gain of 6.7
    # points, one standard error 4.0 points
    live = [(2, 1, 0, True)] * 30
    cand = [(2, 2, 0, True)] * 5 + [(2, 0, 0, True)] + [(2, 1, 0, True)] * 24
    T = _frames({X1280: live, L1920: cand})
    row = next(x for x in decide(T, X1280).rows if x.candidate == L1920)
    assert abs(row.gain - 4 / 60) < 1e-9 and 0.035 < row.noise < 0.045
    assert decide(T, X1280, SwipeParams(noise_z=1.0)).switch          # over one standard error: named
    assert not decide(T, X1280, SwipeParams(noise_z=2.0)).switch      # under two: noise


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
    r = decide(_frames({X1280: [(2, 1, 1, True)] * 40, L1920: [(2, 2, 0, True)] * 40}), X1280)
    assert r.reason.isascii() and "take the empty wall again" in r.reason


def test_no_verdict_when_the_empty_wall_picture_does_not_match_the_wall():
    # the live setting itself puts a box on nobody every other frame: a plate with dancers baked in, or another
    # light -- the referee cannot be trusted to judge the others
    live = [(2, 1, 1, True)] * 20 + [(2, 1, 0, True)] * 20
    T = _frames({X1280: live, L1920: [(2, 2, 0, True)] * 40})
    r = decide(T, X1280)
    assert r.winner is None and not r.switch and "take the empty wall again" in r.reason
    r = decide(T, X1280, SwipeParams(referee_max_loose=1.0))
    assert r.switch and r.winner == L1920


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
    p = SwipeParams(frames=2, covered_frames=0)
    c = FrameCollector(p)
    calls = []

    def grab():
        calls.append(1)
        return np.zeros((4, 4), np.uint8)
    lost = _obs((100, 100, 900))
    assert c.offer(0.0, grab, lost, []) and len(calls) == 1 and c.frames[0].image.shape == (4, 4)
    assert not c.offer(0.2, grab, lost, []) and len(calls) == 1          # too close: not kept, not fetched
    assert not c.offer(1.0, grab, lost, [(80, 60, 120, 140, 0.4)])       # covered, no covered pool
    assert len(calls) == 1


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
