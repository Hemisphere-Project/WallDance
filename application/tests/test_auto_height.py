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
                                                    person_height_max_ratio=2.5, height_guard=True,
                                                    auto_height=False), dancer_height=None,
                           _height_guard=None, height_guard_event=None, _output_clock=lambda: clock[0],
                           _height_owned_px=None, _height_follow=None)
    host._adopt_person_height = lambda old, med, why: FrameProcessor._adopt_person_height(host, old, med, why)
    return host, clock, (lambda dets, inv_lb=1.0: FrameProcessor._guard_person_height(host, dets, inv_lb))


def _det(h, conf=0.9):
    return (np.zeros((17, 2)), np.full(17, conf), (10.0, 10.0, 0.4 * h, h))


def test_guard_replaces_a_stale_height_that_drops_the_dancers():
    host, clock, guard = _guard_host(45)                    # the night project: gate 13-112 px
    for i in range(20):                                     # 1 s: not enough evidence yet
        clock[0] = i / 20.0
        guard([_det(150), _det(160)])
    assert host.settings.person_height_px == 45
    for i in range(20, 60):
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


def test_guard_ignores_a_minority_outside_the_gate():
    # bdx1005-s5: a dancer inside the gate and a confident 569 px person near the camera
    host, clock, guard = _guard_host(138)                   # gate 41-345 px
    for i in range(60):
        clock[0] = i / 20.0
        guard([_det(200), _det(569)])
    assert host.settings.person_height_px == 138 and host.height_guard_event is None


def test_guard_leaves_a_spread_out_population_alone():
    # bdx1005-s8-like: one person walking toward the lens, 120-595 px, all outside a 45 px gate:
    # no single height to adopt (q3 - q1 ~ 0.6 x the median)
    host, clock, guard = _guard_host(45)                    # gate 13-112 px
    for i in range(200):
        clock[0] = i / 20.0
        guard([_det(120 + (i % 20) * 25)])
    assert host.settings.person_height_px == 45 and host.height_guard_event is None


def test_guard_ignores_unsure_skeletons_and_scales_from_the_letterbox():
    host, clock, guard = _guard_host(45)
    for i in range(120):
        clock[0] = i / 20.0
        guard([_det(150, conf=0.3)])                        # no confident head + ankle
    assert host.settings.person_height_px == 45
    for i in range(120, 240):
        clock[0] = i / 20.0
        guard([_det(75)], inv_lb=2.0)                       # 75 px in the YOLO tensor = 150 px
    assert host.settings.person_height_px == 150


def test_the_dancers_height_is_measured_even_with_the_guard_off():
    host, clock, guard = _guard_host(138)
    host.settings.height_guard = False
    for i in range(30):
        clock[0] = i / 20.0
        guard([_det(105), _det(108)])
    med, n, _when = host.dancer_height
    assert 105 <= med <= 108 and n >= 40 and host.settings.person_height_px == 138


# --------------------------------------------------------------------------- #
# Follow (laptop pass 2026-10-07: the guard locked onto the walk-in height)
# --------------------------------------------------------------------------- #
def _run(host, clock, guard, t0, seconds, heights_fn, fps=20.0):
    events = []
    for i in range(int(seconds * fps)):
        clock[0] = t0 + i / fps
        before = host.settings.person_height_px
        guard([_det(h) for h in heights_fn(i)])
        if host.settings.person_height_px != before:
            events.append((round(clock[0], 1), before, host.settings.person_height_px))
    return t0 + seconds, events


def test_guard_follows_the_dancer_from_the_walk_in_to_the_wall():
    host, clock, guard = _guard_host(45)                    # the night project's stale height
    t, ev = _run(host, clock, guard, 0.0, 8, lambda i: [300])          # walk-in near the camera
    assert ev and ev[0][1:] == (45, 300)                    # the first population: the walk-in
    t, ev = _run(host, clock, guard, t, 70, lambda i: [127])           # at the wall
    assert 120 <= host.settings.person_height_px <= 135 and len(ev) == 1
    assert ev[0][0] < 8 + 60                                # within the follow window


def test_guard_follows_a_sparse_far_dancer_after_a_dense_walk_in():
    # s2c-like: ~20 skeletons/s on the walk-in near the camera, then one every 3 s at the dark back wall
    host, clock, guard = _guard_host(45)
    t, ev = _run(host, clock, guard, 0.0, 8, lambda i: [300])
    assert ev and ev[0][1:] == (45, 300)
    t, ev = _run(host, clock, guard, t, 75, lambda i: [127] if i % 60 == 0 else [])
    assert len(ev) == 1 and 120 <= host.settings.person_height_px <= 135
    assert ev[0][0] < 8 + 70


def test_guard_adopts_a_sparse_dark_wall_the_dense_rule_never_sees():
    # s4-like: too few skeletons on the walk-in for the dense rule, then one every 2 s at the dark wall
    # (120 px, outside the stale 45 px gate 13-112): the slow rule adopts it within about a minute
    host, clock, guard = _guard_host(45)
    t, ev = _run(host, clock, guard, 0.0, 10, lambda i: [300] if i % 5 == 0 else [])
    assert ev == []
    t, ev = _run(host, clock, guard, t, 80, lambda i: [120] if i % 40 == 0 else [])
    assert len(ev) == 1 and 115 <= host.settings.person_height_px <= 125


def test_a_configured_height_the_guard_never_set_is_never_followed():
    host, clock, guard = _guard_host(138)                   # white duo: 138 configured, ~200 measured
    _run(host, clock, guard, 0.0, 120, lambda i: [200, 210])
    assert host.settings.person_height_px == 138 and host.height_guard_event is None


def test_a_technician_near_the_lens_is_outvoted_while_the_dancers_are_seen():
    host, clock, guard = _guard_host(45)
    t, _ = _run(host, clock, guard, 0.0, 8, lambda i: [127, 130])      # adopted: the duo at the wall
    assert 125 <= host.settings.person_height_px <= 130
    t, ev = _run(host, clock, guard, t, 90, lambda i: [127, 130, 400])  # + somebody close to the lens
    assert ev == [] and 125 <= host.settings.person_height_px <= 130


def test_no_ping_pong_on_alternating_short_episodes():
    host, clock, guard = _guard_host(45)
    t, _ = _run(host, clock, guard, 0.0, 8, lambda i: [127])
    t, ev = _run(host, clock, guard, t, 180, lambda i: [300] if (i // 200) % 2 else [127])  # 10 s / 10 s
    assert len(ev) <= 1


def test_an_owned_height_is_only_moved_by_the_follow():
    # bdx1005-s5: the dense 10 s rule kept re-adopting the operator near the lens (~430 px) and the follow
    # kept bringing it back to the dancer (~203 px) -- 20 flips; owned, only the follow may move it
    host, clock, guard = _guard_host(45)
    t, ev = _run(host, clock, guard, 0.0, 8, lambda i: [203])
    assert ev and ev[0][1:] == (45, 203)
    for _ in range(3):                                      # 10 s close to the lens, 50 s at the wall
        t, ev1 = _run(host, clock, guard, t, 10, lambda i: [600])
        t, ev2 = _run(host, clock, guard, t, 50, lambda i: [203])
        assert ev1 == [] and ev2 == []
    assert host.settings.person_height_px == 203


def test_ownership_ends_when_someone_else_sets_the_height():
    host, clock, guard = _guard_host(45)
    t, _ = _run(host, clock, guard, 0.0, 8, lambda i: [300])
    host.settings.person_height_px = 150                    # the Advanced slider / a config load
    t, ev = _run(host, clock, guard, t, 90, lambda i: [300])   # 300 sits inside 150's gate (45-375)
    assert ev == [] and host.settings.person_height_px == 150
