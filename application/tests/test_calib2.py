"""Calib2 dancer evidence pool — collector, persistence, pooling, imgsz select."""
import numpy as np
import pytest

import core.calib2 as calib2

from core.calib2 import (SubjectCollector, SubjectPool, SubjectRun, aggregate,
                    select_imgsz)
from core.config import (AUTOCAL2_MIN_SAMPLES, AUTOCAL2_NET_HEIGHT_TARGET,
                    AUTOCAL2_BLUR_BOUNDS_MS, AUTOCAL2_CONF_BOUNDS)


def _make_run(n=60, height=200.0, conf=0.6, speed=4.0, fps=25.0, **kw):
    kw.setdefault("conf_kind", "box")
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
    imgsz, ok, net, fps_limited = select_imgsz(200.0, 1920.0)
    assert imgsz == 1280 and ok and not fps_limited
    assert net == pytest.approx(200.0 * 1280 / 1920.0)
    assert net >= AUTOCAL2_NET_HEIGHT_TARGET


def test_select_imgsz_big_dancer_small_size():
    # 600 px dancer in 1920: 640 already gives 200 net px.
    imgsz, ok, _, _ = select_imgsz(600.0, 1920.0)
    assert imgsz == 640 and ok


def test_select_imgsz_unsatisfiable_flags():
    # 40 px dancer in 1920: even 1920 gives only 40 net px.
    imgsz, ok, net, fps_limited = select_imgsz(40.0, 1920.0)
    assert imgsz == 1920 and not ok
    assert not fps_limited            # no budget given → not an fps story
    assert net < AUTOCAL2_NET_HEIGHT_TARGET


def test_select_imgsz_fps_budget_caps_preset():
    # Height target wants 1280+, but the rig only sustains the budget at <=960
    # (measured 25 fps @ 1280 → 1280 in budget? model: fps(p) = 25*(1280/p)^2;
    # fps(1280)=25 ok, so make it slower: 12 fps @ 1280).
    def fps_model(p):
        return 12.0 * (1280.0 / p) ** 2
    # fps: 640→48, 800→30.7, 960→21.3, 1280→12, ... budget 20 → allowed ≤960.
    imgsz, ok, net, fps_limited = select_imgsz(
        200.0, 1920.0, fps_model=fps_model, fps_budget=20.0)
    assert imgsz == 960
    assert not ok                     # net = 200*960/1920 = 100 < 110 target
    assert fps_limited                # 1280 would have met it but blew the budget
    assert net == pytest.approx(100.0)


def test_select_imgsz_fps_budget_unmeetable_takes_smallest():
    def fps_model(p):
        return 5.0                    # rig can't meet budget at any preset
    imgsz, ok, net, fps_limited = select_imgsz(
        200.0, 1920.0, fps_model=fps_model, fps_budget=20.0)
    assert imgsz == 640 and not ok and fps_limited


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


# ------------------------------------------------------- ⑤a box-conf upgrade

def test_collector_stamps_box_kind_and_imgsz():
    c = SubjectCollector(window_frames=2)
    c.start("live", "show", (0, 0, 640, 480), (640, 480), imgsz=1280)
    assert c.run.conf_kind == "box"
    assert c.run.imgsz == 1280


def test_collector_none_conf_keeps_height_and_speed():
    # A bridge/cold-blob-fed track has no YOLO box conf this frame — its
    # height + speed still count, the conf pool just gets no sample.
    c = SubjectCollector(window_frames=1)
    c.start("live", "show", (0, 0, 640, 480), (640, 480))
    c.feed([(180.0, None, 3.0), (220.0, 0.5, 6.0)], fps_sample=24.0)
    assert c.run.heights == [180.0, 220.0]
    assert c.run.confs == [0.5]
    assert c.run.speeds == [3.0, 6.0]


def test_legacy_run_loads_as_kpt_kind_and_gives_no_seed():
    # Pre-⑤ run files lack conf_kind → must load as the legacy kind, and the
    # pooled seed must NOT mix keypoint-conf units (they pinned the clamp).
    legacy = SubjectRun.from_json({
        "timestamp": "t", "heights": [200.0] * 60, "confs": [0.9] * 60,
        "speeds": [4.0] * 60, "fps": [25.0] * 5, "frames": 60,
    })
    assert legacy.conf_kind == "kpt_mean"
    prop = aggregate([legacy], 1920.0)
    assert prop.ok
    assert prop.confidence is None
    assert "box-confidence upgrade" in prop.note
    assert "box-confidence upgrade" in prop.summary()


def test_mixed_pool_seeds_from_box_runs_only():
    legacy = _make_run(n=60, conf=0.95, conf_kind="kpt_mean")  # would pin high
    box = _make_run(n=60, conf=0.40)
    prop = aggregate([legacy, box], 1920.0)
    assert prop.confidence == pytest.approx(0.35, abs=0.01)   # 0.40 - margin


# ------------------------------------------------------- ⑤c fps cap pooling

def test_aggregate_fps_caps_imgsz_and_reports_prediction():
    # Dancer 200px in 1920 wants 1280, but the run measured 12 fps at 1280 →
    # predicted fps(1280) = 12 < budget 20 → capped to 960 with the advisory.
    run = _make_run(n=60, height=200.0, fps=12.0, imgsz=1280)
    prop = aggregate([run], 1920.0)
    assert prop.imgsz == 960
    assert prop.imgsz_fps_limited
    assert not prop.imgsz_satisfied
    assert prop.imgsz_pred_fps == pytest.approx(12.0 * (1280 / 960) ** 2, abs=0.2)
    assert "RIG ADVISORY" in prop.summary()
    assert "capped by FPS budget" in prop.summary()


def test_aggregate_legacy_runs_without_imgsz_skip_the_cap():
    # imgsz=0 (legacy) → no fps model → old behavior (uncapped pick).
    run = _make_run(n=60, height=200.0, fps=12.0)   # imgsz defaults to 0
    prop = aggregate([run], 1920.0)
    assert prop.imgsz == 1280
    assert not prop.imgsz_fps_limited
    assert prop.imgsz_pred_fps is None


# ------------------------------------------ Phase 2b: dark target (high noise)

def test_aggregate_dark_noise_switches_to_small_target():
    # 200 px dancer in 1920: normal target 110 → 1280; dark target 45 →
    # need imgsz >= 45*1920/200 = 432 → 640. Quality measured INVERTING on
    # dark scenes (tmp_analysis/phase2b: dark-crowd 0.19@640 vs 0.96@1536).
    run = _make_run(n=60, height=200.0)
    normal = aggregate([run], 1920.0, noise_sigma=2.0)
    dark = aggregate([run], 1920.0, noise_sigma=6.0)
    assert normal.imgsz == 1280 and not normal.imgsz_dark_mode
    assert dark.imgsz == 640 and dark.imgsz_dark_mode
    assert dark.imgsz_satisfied
    assert dark.net_target_px == pytest.approx(45.0)
    assert "dark scene" in dark.summary()
    assert "dark scene" not in normal.summary()


def test_aggregate_no_noise_evidence_keeps_normal_target():
    run = _make_run(n=60, height=200.0)
    prop = aggregate([run], 1920.0)            # noise_sigma omitted
    assert prop.imgsz == 1280 and not prop.imgsz_dark_mode


def test_dark_rig_advisory_uses_dark_target():
    # Tiny dancer + dark: even the dark target can be unreachable; the
    # advisory must quote the active (dark) target, not the 110 default.
    run = _make_run(n=60, height=30.0)
    prop = aggregate([run], 1920.0, noise_sigma=6.0)
    assert not prop.imgsz_satisfied
    assert "~45 px" in prop.summary()


# -------------------------------- P-6: per-rig fps table + model advisory

_TABLE = {
    "yolo11x-pose": {640: 40.0, 960: 30.0, 1280: 18.0},
    "yolo11l-pose": {640: 60.0, 960: 45.0, 1280: 27.0},
    "yolo11m-pose": {640: 80.0, 960: 60.0, 1280: 36.0},
}


def test_load_fps_table_roundtrip_and_garbage(tmp_path):
    p = tmp_path / "fps_table.json"
    p.write_text('{"_meta": {"runs": 30}, "yolo11m-pose": {"960": 60.0},'
                 ' "bad": "nope"}')
    table = calib2.load_fps_table(str(p))
    assert table == {"yolo11m-pose": {960: 60.0}}
    assert calib2.load_fps_table(str(tmp_path / "missing.json")) is None
    (tmp_path / "junk.json").write_text("{not json")
    assert calib2.load_fps_table(str(tmp_path / "junk.json")) is None


def test_aggregate_uses_table_cost_curve_over_quadratic():
    # Live 12 fps at 1280; table says 1280→18 / 960→30 for the current model:
    # predicted fps(960) = 12 * 30/18 = 20 — exactly meets the budget, so the
    # height-target pick (1280→12 fps) is capped to 960. The quadratic law
    # would have predicted 21.3 — same pick here, but the prediction must be
    # the table's.
    run = _make_run(n=60, height=200.0, fps=12.0, imgsz=1280)
    prop = aggregate([run], 1920.0, fps_table=_TABLE,
                     current_model="yolo11x-pose")
    assert prop.imgsz == 960
    assert prop.imgsz_pred_fps == pytest.approx(20.0, abs=0.1)


def test_model_advisory_suggests_larger_tier_in_budget():
    # Current 11m at 960 with live 40 fps; table ratios put 11x@960 at
    # 40*30/60 = 20 fps — the largest tier inside the budget → advise 11x.
    run = _make_run(n=60, height=400.0, fps=40.0, imgsz=960)
    prop = aggregate([run], 1920.0, fps_table=_TABLE,
                     current_model="yolo11m-pose")
    assert prop.imgsz == 640                  # 400px dancer: 640 meets 110
    best, pred = calib2.advise_model(_TABLE, "yolo11m-pose", 640,
                                     [(40.0, 960)])
    assert best == "yolo11x-pose"             # 40*40/60 = 26.7 >= 20
    assert pred == pytest.approx(26.7, abs=0.1)
    assert "yolo11x-pose" in prop.model_advisory


def test_model_advisory_silent_when_current_is_best():
    best, _ = calib2.advise_model(_TABLE, "yolo11x-pose", 640, [(40.0, 960)])
    assert best == "yolo11x-pose"
    run = _make_run(n=60, height=400.0, fps=40.0, imgsz=960)
    prop = aggregate([run], 1920.0, fps_table=_TABLE,
                     current_model="yolo11x-pose")
    assert prop.model_advisory == ""


def test_model_advisory_nothing_fits_reports_starvation():
    # Live 4 fps: no tier reaches 20 fps at 1280.
    best, pred = calib2.advise_model(_TABLE, "yolo11x-pose", 1280,
                                     [(4.0, 1280)])
    assert best is None and pred is not None
    run = _make_run(n=60, height=200.0, fps=4.0, imgsz=1280)
    prop = aggregate([run], 1920.0, fps_table=_TABLE,
                     current_model="yolo11x-pose")
    assert "no yolo11 tier" in prop.model_advisory


def test_advisory_absent_without_table_or_model():
    run = _make_run(n=60, height=200.0, fps=12.0, imgsz=1280)
    assert aggregate([run], 1920.0).model_advisory == ""
    assert aggregate([run], 1920.0, fps_table=_TABLE).model_advisory == ""
    assert calib2.advise_model(None, "yolo11m-pose", 960, [(40.0, 960)]) \
        == (None, None)
    assert calib2.advise_model(_TABLE, "yolo26m-pose", 960, [(40.0, 960)]) \
        == (None, None)
