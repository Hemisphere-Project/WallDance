"""Empty-wall YOLO check (core/empty_wall.py, D29): the strongest enhancement YOLO sees nobody with."""
import pytest

from core.empty_wall import NEUTRAL_GAMMA, EmptyWallCheck, PersonWatch, Sighting, ladder


def test_ladder_steps_gamma_down_to_the_floor_then_clahe():
    lad = ladder(1.8, 2.5)
    gammas = [g for g, _c in lad]
    assert gammas[0] == pytest.approx(1.8) and gammas == sorted(gammas, reverse=True)
    assert gammas[3:5] == [pytest.approx(1.0), pytest.approx(0.8)]
    assert lad[-2:] == [(pytest.approx(0.8), 1.5), (pytest.approx(0.8), 1.0)]
    assert ladder(0.73, 2.5) == [(0.73, 2.5), (0.73, 1.5), (0.73, 1.0)]   # already dark: CLAHE only
    assert ladder(1.02, 1.5)[:2] == [(1.02, 1.5), (0.8, 1.5)]              # near-equal gammas merged


def _run(chk, ghost_at):
    """ghost_at(gamma, clahe) -> conf of a still ghost (0 = none); returns the applied sequence."""
    cur = chk.current()
    applied = [cur]
    for _ in range(500):
        if chk.done:
            break
        conf = ghost_at(*cur)
        nxt = chk.feed([(conf, 300.0, 400.0, 150.0)] if conf > 0 else [])
        if nxt is not None:
            cur = nxt
            applied.append(cur)
    return applied


def test_a_clean_wall_keeps_the_calibrated_enhancement():
    chk = EmptyWallCheck(1.8, 1.5, confidence=0.25)
    applied = _run(chk, lambda g, c: 0.0)
    assert applied == [(1.8, 1.5)] and chk.result.clean
    assert chk.result.gamma == pytest.approx(1.8) and chk.tried[0].frames == 120


def test_a_ghost_under_strong_gamma_steps_down_until_yolo_sees_nobody():
    # the night project: a "person" by the equipment at 0.30 under gamma 1.8, gone below gamma 1.3
    chk = EmptyWallCheck(1.8, 1.5, confidence=0.25)
    applied = _run(chk, lambda g, c: 0.30 if g > 1.3 else 0.12)
    r = chk.result
    assert r.clean and r.gamma == pytest.approx(1 + 0.8 / 3)
    assert len(applied) == 3 and chk.tried[0].ghost_frames == 1            # a ghost fails fast
    assert chk.tried[-1].max_conf == pytest.approx(0.12)                   # below 0.8 x 0.25: allowed
    assert "ok" in r.summary() and "1/" in r.summary()


def test_the_margin_leaves_room_for_the_sensitivity_dial():
    chk = EmptyWallCheck(1.0, 2.5, confidence=0.25)                        # limit 0.20
    _run(chk, lambda g, c: 0.22 if c > 2.0 else 0.0)
    assert chk.result.clean and chk.result.clahe == 1.5


def test_a_person_at_every_setting_stops_at_neutral_and_names_the_place():
    # X3 (was: "a ghost at every setting keeps the weakest" -- 08/10 20:06, a dancer in view walked the
    # mur project down to gamma 0.8 / CLAHE 1.0).  Seen in every frame at every rung = a person: the ladder
    # stops at neutral, nothing clean to keep, and says where; A6 ③: no "paint an exclusion" remedy.
    chk = EmptyWallCheck(1.8, 2.5, confidence=0.25, region=(0, 0, 1200, 900))
    applied = _run(chk, lambda g, c: 0.4)
    r = chk.result
    assert not r.clean and r.person is not None and r.ceiling is None
    assert min(g for g, _c in applied) == pytest.approx(NEUTRAL_GAMMA)          # never under neutral
    assert (r.person.x, r.person.y, r.person.h) == (300.0, 400.0, 150.0)
    assert chk.tried[-1].watched == 6 and chk.tried[-1].person_hits == 6         # decided early
    s = r.summary()
    assert "middle left of the wall (x 300, y 400)" in s and "Calibrate again" in s
    assert "exclusion" not in s and "paint" not in s
    assert "stopped: person at x=300 y=400" in r.log_line()


def test_a_ghost_at_every_setting_that_is_no_person_keeps_the_weakest_and_names_the_place():
    # a sporadic false person (1 frame in 10) at every setting: no person, so the ladder goes on down
    chk = EmptyWallCheck(1.8, 2.5, confidence=0.25)
    n = [0]

    def sporadic(g, c):
        n[0] += 1
        return 0.4 if n[0] % 10 == 0 else 0.0
    applied = _run(chk, sporadic)
    r = chk.result
    assert not r.clean and r.person is None and (r.gamma, r.clahe) == (pytest.approx(0.8), 1.0)
    assert applied[-1] == (pytest.approx(0.8), 1.0)
    assert all(c.watched and c.person_hits <= 3 for c in chk.tried if c.gamma <= 1.0)
    s = r.summary()
    assert "x=300, y=400" in s and "Region of Interest" in s and "Calibrate again" in s
    assert "exclusion" not in s and "paint" not in s


def test_the_watch_can_be_turned_off_and_the_ladder_walks_down_as_before():
    chk = EmptyWallCheck(1.8, 2.5, confidence=0.25, person_frames=0)
    _run(chk, lambda g, c: 0.4)
    assert not chk.result.clean and chk.result.person is None
    assert (chk.result.gamma, chk.result.clahe) == (pytest.approx(0.8), 1.0)


def test_the_ceiling_is_the_strongest_clean_rung():
    chk = EmptyWallCheck(1.8, 1.5, confidence=0.25)
    _run(chk, lambda g, c: 0.30 if g > 1.3 else 0.0)
    assert chk.result.ceiling == (pytest.approx(1 + 0.8 / 3), 1.5)
    kept = EmptyWallCheck(1.8, 1.5, confidence=0.15, keep=(0.73, 2.5))
    _run(kept, lambda g, c: 0.0)
    assert kept.result.ceiling == (0.73, 2.5) and kept.result.kept_current


def test_a_person_who_walks_in_during_a_clean_rung_at_neutral_stops_the_check():
    # the rung at neutral was clean for 50 frames, then someone walks in and stays
    chk = EmptyWallCheck(1.0, 1.5, confidence=0.25)
    n = [0]

    def walks_in(g, c):
        n[0] += 1
        return 0.6 if n[0] > 54 else 0.0
    applied = _run(chk, walks_in)
    assert chk.result.person is not None and applied == [(1.0, 1.5)]


def test_person_watch_tells_a_dancer_from_a_stray_ghost():
    stray = PersonWatch(0.15, frames=30, min_frac=0.2)
    for i in range(30):
        stray.feed([(0.15, 10.0, 20.0, 50.0)] if i == 12 else [(0.10, 10.0, 20.0, 50.0)])
        if stray.done:
            break
    assert stray.person is False and stray.hits == 1 and stray.seen == 26      # decided once 6 hits are out of reach
    dancer = PersonWatch(0.15, frames=30, min_frac=0.2, settle=2)
    xs = [500.0, 510.0, 520.0, 530.0, 540.0, 550.0]
    for i in range(40):
        dancer.feed([(0.5, xs[i // 2 % 6], 600.0, 110.0), (0.2, 50.0, 50.0, 80.0)] if i % 2 else [])
        if dancer.done:
            break
    assert dancer.person is True and dancer.hits == 6 and dancer.seen == 12     # 2 settle, then 12 watched
    s = dancer.sighting
    assert (s.y, s.h, s.conf, s.hits) == (600.0, 110.0, 0.5, 6) and 500.0 <= s.x <= 550.0


def test_two_dancers_side_by_side_give_one_of_their_places_not_the_midpoint():
    # mur 3 at f1300: two dancers hang 170 px apart (527, 576) and (698, 442), body height ~104 px; the
    # strongest box alternates between them
    w = PersonWatch(0.15, frames=30, min_frac=0.5)
    a, b = (0.55, 527.0, 576.0, 104.0), (0.60, 698.0, 442.0, 100.0)
    for i in range(30):
        w.feed([a, (0.3, 698.0, 442.0, 100.0)] if i % 3 else [b, (0.3, 527.0, 576.0, 104.0)])
    s = w.sighting
    assert w.person and (s.x, s.y) == (527.0, 576.0) and s.conf == 0.55


def test_sighting_where_names_the_part_of_the_wall():
    s = Sighting(x=1000.0, y=200.0, h=100.0, conf=0.6, hits=8, frames=10)
    roi = (135, 3, 965, 1771)
    assert s.where(roi) == "top right of the wall (x 1000, y 200)"
    assert Sighting(617, 900, 100, 0.6, 8, 10).where(roi) == "centre of the wall (x 617, y 900)"
    assert Sighting(50, 900, 100, 0.6, 8, 10).where(roi).startswith("outside the Region of Interest")
    assert s.where(None) == "at x 1000, y 200"


def test_a_single_stray_ghost_frame_fails_the_rung():
    # gamma 1.0 on the bright empty take: 1 stray in 40 frames -- the same rare false person that later
    # stole the dancer's track on the dark takes; the rung must fail and the next one (0.8) be kept
    chk = EmptyWallCheck(1.0, 1.5, confidence=0.15)
    n = [0]

    def stray(g, c):
        n[0] += 1
        return 0.15 if g > 0.9 and n[0] == 20 else 0.0
    _run(chk, stray)
    assert chk.result.clean and chk.result.gamma == pytest.approx(0.8)
    assert chk.tried[0].ghost_frames == 1 and 0.0 < chk.progress() <= 1.0
    chk2 = EmptyWallCheck(1.5, 2.5, confidence=0.25, max_ghost_frames=1)   # the tolerance stays a knob
    m = [0]

    def one(g, c):
        m[0] += 1
        return 0.3 if m[0] == 20 else 0.0
    _run(chk2, one)
    assert chk2.result.clean and chk2.result.gamma == pytest.approx(1.5)


def test_a_calibrated_enhancement_that_passes_is_kept_before_the_scene_pick():
    # the night project: calibrated 0.73 / 2.5; the scene formula picks 1.8 / 1.5 on the empty take
    chk = EmptyWallCheck(1.8, 1.5, confidence=0.15, keep=(0.73, 2.5))
    assert chk.current() == (0.73, 2.5)
    applied = _run(chk, lambda g, c: 0.3 if g > 1.3 else 0.0)
    assert applied == [(0.73, 2.5)] and chk.result.clean and chk.result.kept_current
    assert "Kept the current gamma 0.73" in chk.result.summary()


def test_a_calibrated_enhancement_with_ghosts_falls_back_to_the_ladder():
    chk = EmptyWallCheck(1.8, 1.5, confidence=0.15, keep=(1.5, 2.5))
    _run(chk, lambda g, c: 0.3 if c > 2.0 or g > 1.3 else 0.0)
    r = chk.result
    assert r.clean and not r.kept_current and r.gamma < 1.3 and chk.tried[0].clahe == 2.5
