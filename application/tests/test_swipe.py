"""The settings swipe's referee, collector and verdict (Plan Cordée P5) -- no GPU, no video."""
import numpy as np

from core.swipe import (Candidate, FrameCollector, Observation, SwipeParams, Tally, bare_groups, decide,
                        score_frame)

X1280 = Candidate("yolo11x-pose", 1280)
L1920 = Candidate("yolo11l-pose", 1280, (1920, 1056))


def _obs(*groups):
    return Observation(groups=sorted(groups, key=lambda g: -g[2]))


def test_labels_and_engine_names():
    assert X1280.label == "x @ 1280" and X1280.engine_file == "yolo11x-pose_1280.engine"
    assert L1920.label == "l @ 1920 x 1056" and L1920.engine_file == "yolo11l-pose_1920x1056.engine"


def test_a_group_is_covered_by_a_box_around_its_centre():
    p = SwipeParams()
    obs = _obs((100, 100, 900), (400, 400, 500), (700, 700, 50))      # the last one is too small to be a dancer
    boxes = [(80, 60, 120, 140, 0.4), (600, 600, 640, 660, 0.3), (395, 300, 405, 380, 0.05)]
    s = score_frame(obs, boxes, p)
    assert (s.groups, s.covered, s.boxes, s.loose) == (2, 1, 2, 1)
    assert bare_groups(obs, boxes, p) == [(400, 400, 500)]


def test_the_collector_keeps_lost_frames_first_and_spaces_them():
    p = SwipeParams(frames=3, min_gap_s=0.5)
    c = FrameCollector(p)
    img = np.zeros((4, 4), np.uint8)
    lost, held = _obs((100, 100, 900)), [(80, 60, 120, 140, 0.4)]
    assert not c.offer(0.0, img, None, [])                    # no valid foreground
    assert not c.offer(0.0, img, _obs((5, 5, 20)), [])        # nobody on the wall
    assert c.offer(0.0, img, lost, held)                      # a covered dancer: kept as filler
    assert not c.offer(0.2, img, lost, [])                    # too close to the last kept frame
    for t in (1.0, 2.0, 3.0):
        assert c.offer(t, img, lost, [])
    assert c.ready and c.lost_frames == 3
    assert not c.offer(4.0, img, lost, [])                    # full
    assert [f.lost for f in c.frames] == [True, True, True]


def _tally(groups, covered, loose=0, frames=30):
    t = Tally()
    t.frames, t.groups, t.covered, t.loose = frames, groups, covered, loose
    return t


def test_the_verdict_names_a_clear_winner_and_keeps_the_live_setting_otherwise():
    r = decide({X1280: _tally(40, 10), L1920: _tally(40, 36)}, X1280)
    assert r.switch and r.winner == L1920 and r.rows[0].candidate == L1920
    r = decide({X1280: _tally(40, 35), L1920: _tally(40, 36)}, X1280)
    assert not r.switch and r.winner == X1280
    r = decide({X1280: _tally(40, 10), L1920: _tally(40, 36, loose=30)}, X1280)
    assert not r.switch and not r.rows[-1].ok                 # boxes on nobody set the candidate aside
    r = decide({X1280: _tally(5, 1), L1920: _tally(5, 5)}, X1280)
    assert r.winner is None and not r.switch                  # too few dancers to say
