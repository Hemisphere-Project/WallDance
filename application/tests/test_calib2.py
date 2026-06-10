"""Calib2 dancer evidence pool — collector, persistence, pooling, imgsz select."""
import numpy as np
import pytest

import calib2
from calib2 import (SubjectCollector, SubjectPool, SubjectRun, aggregate,
                    select_imgsz)
from config import (AUTOCAL2_MIN_SAMPLES, AUTOCAL2_NET_HEIGHT_TARGET,
                    AUTOCAL2_BLUR_BOUNDS_MS, AUTOCAL2_CONF_BOUNDS)


def _make_run(n=60, height=200.0, conf=0.6, speed=4.0, fps=25.0, **kw):
    run = SubjectRun(timestamp="t", source="live", profile="show",
                     roi=(0, 0, 1920, 1080), roi_source=(1920, 1080), **kw)
    run.heights = [height] * n
    run.confs = [conf] * n
    run.speeds = [speed] * n
    run.fps = [fps] * 10
    run.frames = n
    return run


# ------------------------------------------------------------ collector

def test_collector_window_and_samples():
    c = SubjectCollector(window_frames=5)
    c.start("live", "show", (0, 0, 640, 480), (640, 480))
    assert c.is_collecting and not c.ready
    for _ in range(5):
        c.feed([(180.0, 0.7, 3.0), (220.0, 0.5, 6.0)], fps_sample=24.0)
    assert c.ready
    run = c.finish()
    assert run.samples == 10
    assert run.frames == 5
    assert not c.is_collecting


def test_collector_ignores_bad_heights():
    c = SubjectCollector(window_frames=2)
    c.start("live", "show", (0, 0, 64, 48), (64, 48))
    c.feed([(0.0, 0.5, 1.0), (-5.0, 0.5, 1.0), (100.0, 0.5, 1.0)], 30.0)
    assert c.run.samples == 1


# ------------------------------------------------------------ persistence

def test_pool_save_load_clear(tmp_path):
    pool = SubjectPool(str(tmp_path))
    run = _make_run()
    path = pool.save_run(run)
    loaded = pool.load_runs()
    assert len(loaded) == 1
    lpath, lrun = loaded[0]
    assert lpath == path
    assert lrun.heights == run.heights
    assert lrun.roi == (0, 0, 1920, 1080)
    assert pool.clear() == 1
    assert pool.load_runs() == []


# ------------------------------------------------------------ staleness

def test_stale_on_framing_change():
    run = _make_run()
    assert not run.stale_for((0, 0, 1920, 1080), (1920, 1080))
    assert not run.stale_for((0, 0, 1820, 1080), (1920, 1080))   # 5% — fine
    assert run.stale_for((0, 0, 960, 540), (1920, 1080))         # halved → stale


# ------------------------------------------------------------ imgsz select

def test_select_imgsz_meets_target():
    # 200 px dancer in a 1920 ROI: need imgsz >= 110*1920/200 = 1056 → 1280.
    imgsz, ok, net = select_imgsz(200.0, 1920.0)
    assert imgsz == 1280 and ok
    assert net == pytest.approx(200.0 * 1280 / 1920.0)
    assert net >= AUTOCAL2_NET_HEIGHT_TARGET


def test_select_imgsz_big_dancer_small_size():
    # 600 px dancer in 1920: 640 already gives 200 net px.
    imgsz, ok, _ = select_imgsz(600.0, 1920.0)
    assert imgsz == 640 and ok


def test_select_imgsz_unsatisfiable_flags():
    # 40 px dancer in 1920: even 1920 gives only 40 net px.
    imgsz, ok, net = select_imgsz(40.0, 1920.0)
    assert imgsz == 1920 and not ok
    assert net < AUTOCAL2_NET_HEIGHT_TARGET


# ------------------------------------------------------------ aggregation

def test_aggregate_pools_across_runs():
    runs = [_make_run(n=40, height=180.0), _make_run(n=40, height=220.0)]
    prop = aggregate(runs, roi_long_side=1920.0)
    assert prop.ok
    assert prop.samples == 80
    assert prop.person_height_px == 200      # pooled median of 180/220
    assert prop.imgsz == 1280
    assert AUTOCAL2_CONF_BOUNDS[0] <= prop.confidence <= AUTOCAL2_CONF_BOUNDS[1]


def test_aggregate_needs_min_samples():
    prop = aggregate([_make_run(n=AUTOCAL2_MIN_SAMPLES - 1)], 1920.0)
    assert not prop.ok
    assert "not ready" in prop.summary()


def test_aggregate_blur_budget_from_speed():
    # 200 px dancer at 8 px/frame, 25 fps → 0.2 px/ms → budget = 20/0.2 = 100 ms
    # → clamped to the upper bound.
    fast = _make_run(n=60, height=200.0, speed=80.0, fps=25.0)
    prop = aggregate([fast], 1920.0)
    # 80 px/frame * 25 fps / 1000 = 2 px/ms → 20 px allowed / 2 = 10 ms budget
    assert prop.blur_budget_ms == pytest.approx(10.0)
    slow = _make_run(n=60, height=200.0, speed=1.0, fps=25.0)
    prop2 = aggregate([slow], 1920.0)
    assert prop2.blur_budget_ms == pytest.approx(AUTOCAL2_BLUR_BOUNDS_MS[1])


def test_aggregate_confidence_seed_from_low_percentile():
    runs = [_make_run(n=60, conf=0.8)]
    runs[0].confs[:6] = [0.3] * 6     # weakest 10% at 0.3 → p05 = 0.3
    prop = aggregate(runs, 1920.0)
    assert prop.confidence == pytest.approx(0.25, abs=0.01)  # p05 - margin
