"""Empty-wall YOLO check (core/empty_wall.py, D29): the strongest enhancement YOLO sees nobody with."""
import pytest

from core.empty_wall import EmptyWallCheck, ladder


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


def test_a_ghost_at_every_setting_keeps_the_weakest_and_names_the_place():
    chk = EmptyWallCheck(1.8, 2.5, confidence=0.25)
    _run(chk, lambda g, c: 0.4)
    r = chk.result
    assert not r.clean and (r.gamma, r.clahe) == (pytest.approx(0.8), 1.0)
    assert "x=300, y=400" in r.summary() and "exclusion" in r.summary()


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
