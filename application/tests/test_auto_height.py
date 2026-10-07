"""Automatic person height (core/auto_height.py): learned from confident full skeletons."""
from types import SimpleNamespace

import numpy as np

from core.auto_height import AutoHeight


def _st(h, conf=0.9, fss=0):
    kc = np.full(17, conf)
    return SimpleNamespace(frames_since_skeleton=fss, confidence=kc, bbox=np.array([0, 0, 0.4 * h, h]))


def test_learns_the_median_of_confident_full_skeletons():
    ah = AutoHeight(min_samples=10)
    out = None
    for i in range(40):
        out = ah.update([_st(150 + (i % 5)), _st(400, conf=0.2), _st(300, fss=3)], i / 20.0, current=45)
    assert out is not None and 149 <= out <= 155            # unsure / stale skeletons ignored


def test_moves_slowly_after_the_first_estimate_and_forgets_old_samples():
    ah = AutoHeight(min_samples=5, window_s=2.0, rate=0.5)
    for i in range(20):
        first = ah.update([_st(150)], i / 20.0, current=45)
    assert abs(first - 150) < 1
    for i in range(20, 200):                                 # the dancers move closer: 300 px
        out = ah.update([_st(300)], i / 20.0, current=first)
    assert 250 < out <= 300
    assert ah.update([], 100.0, current=out) is None         # nothing recent: keep the current value


# --------------------------------------------------------------------------- #
# Height guard (FrameProcessor._guard_person_height, D29): raw detections, gate-only
# --------------------------------------------------------------------------- #
def _guard_host(ph):
    from core.pipeline import FrameProcessor
    clock = [0.0]
    host = SimpleNamespace(settings=SimpleNamespace(person_height_px=ph, person_height_min_ratio=0.3,
                                                    person_height_max_ratio=2.5),
                           _height_guard=None, height_guard_event=None, _output_clock=lambda: clock[0])
    return host, clock, (lambda dets, inv_lb=1.0: FrameProcessor._guard_person_height(host, dets, inv_lb))


def _det(h, conf=0.9):
    return (np.zeros((17, 2)), np.full(17, conf), (10.0, 10.0, 0.4 * h, h))


def test_guard_replaces_a_stale_height_that_drops_the_dancers():
    host, clock, guard = _guard_host(45)                    # the night project: gate 13-112 px
    for i in range(30):
        clock[0] = i / 20.0
        guard([_det(150), _det(160)])                       # a duo at the wall, both detected
    assert 150 <= host.settings.person_height_px <= 160
    assert host.height_guard_event[0] == 45


def test_guard_never_touches_a_height_whose_gate_holds_the_dancers():
    host, clock, guard = _guard_host(138)                   # white duo: configured 138, bodies ~200
    for i in range(60):
        clock[0] = i / 20.0
        guard([_det(200), _det(210)])
    assert host.settings.person_height_px == 138 and host.height_guard_event is None


def test_guard_ignores_unsure_skeletons_and_scales_from_the_letterbox():
    host, clock, guard = _guard_host(45)
    for i in range(30):
        clock[0] = i / 20.0
        guard([_det(150, conf=0.3)])                        # no confident head + ankle
    assert host.settings.person_height_px == 45
    for i in range(30, 60):
        clock[0] = i / 20.0
        guard([_det(75)], inv_lb=2.0)                       # 75 px in the YOLO tensor = 150 px
    assert host.settings.person_height_px == 150
