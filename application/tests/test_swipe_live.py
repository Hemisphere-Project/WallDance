"""The settings swipe's live part (Plan Cordée P5): the pipeline's tap, the candidate runner's coordinates,
the controller's states, the readiness row, Apply.  CPU only, fake models, no video."""
import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from core.input_shape import RECT_PRESETS
from core.ops_monitor import check_swipe
from core.swipe import Candidate, Observation, SwipeParams, decide, Tally, score_frame
from core.swipe_runner import (CandidatePlan, CandidateRun, SwipeInput, boxes_from_results,
                               parse_candidate, plan_candidates, recommended_conf, run_swipe,
                               unletterbox_xyxy)

X1280 = Candidate("yolo11x-pose", 1280)
L1280 = Candidate("yolo11l-pose", 1280)
X1760 = Candidate("yolo11x-pose", 1280, (1760, 960))
L1760 = Candidate("yolo11l-pose", 1280, (1760, 960))
X1920 = Candidate("yolo11x-pose", 1280, (1920, 1056))
L1920 = Candidate("yolo11l-pose", 1280, (1920, 1056))
FACADE_ROI = (135, 3, 965, 1771)          # 08/10 mur2: x, y, w, h in the 1260 x 1824 portrait frame


# ----------------------------------------------------------------------------------------------------
# candidates
# ----------------------------------------------------------------------------------------------------
def test_candidate_labels_parse():
    assert parse_candidate("x@1280", 1280) == X1280
    assert parse_candidate("l@1920x1056", 1280) == L1920
    assert parse_candidate("yolo11l-pose@1760x960", 1280) == L1760
    with pytest.raises(ValueError):
        parse_candidate("l1920", 1280)


def test_plan_mur2_small_dancer_both_models_at_the_largest_input():
    """mur2: the dancer ~78 px at 1280^2, ROI 965 x 1771 -> x @ 1280 (live), x @ 1920 x 1056, l @ 1920 x 1056."""
    plan = plan_candidates(X1280, 965, 1771, lambda c: True, dancer_net_px=78.0)
    assert plan.candidates == [X1280, X1920, L1920]
    texts = plan.skipped_text()
    assert "l @ 1280 not tried: the dancer is only 78 px tall there" in texts
    assert any(t.startswith("x @ 1760 x 960 not tried: an intermediate input") for t in texts)
    assert all(t.isascii() for t in texts)


def test_plan_mid_height_adds_the_other_model_at_the_live_input():
    plan = plan_candidates(X1280, 965, 1771, lambda c: True, dancer_net_px=150.0)
    assert plan.candidates == [X1280, X1920, L1920, L1280]


def test_plan_tall_dancer_only_the_model_is_in_question():
    plan = plan_candidates(X1280, 965, 1771, lambda c: True, dancer_net_px=230.0)
    assert plan.candidates == [X1280, L1280]
    assert all("~230 px tall at x @ 1280" in why for _c, why in plan.skipped)


def test_plan_unknown_height_counts_as_small():
    plan = plan_candidates(X1280, 965, 1771, lambda c: True, dancer_net_px=None)
    assert plan.candidates == [X1280, X1920, L1920]
    assert any("not measured" in t for t in plan.skipped_text())


def test_plan_without_1920_engines_takes_the_largest_built_input():
    plan = plan_candidates(X1280, 965, 1771, lambda c: c.shape != (1920, 1056), dancer_net_px=78.0)
    assert plan.candidates == [X1280, X1760, L1760]
    assert "l @ 1920 x 1056 not tried: its engine is not built on this machine " \
           "(yolo11l-pose_1920x1056.engine)" in plan.skipped_text()


def test_plan_square_roi_and_a_live_rectangle():
    plan = plan_candidates(X1280, 1200, 1100, lambda c: True, dancer_net_px=78.0)
    assert plan.candidates == [X1280, L1280]                       # nearly square ROI: the square only
    plan = plan_candidates(L1920, 965, 1771, lambda c: True, dancer_net_px=120.0)
    assert plan.candidates == [L1920, X1920]                       # the live input is already the largest


def test_plan_only_and_all_sizes_bypass_the_rule():
    assert plan_candidates(X1280, 965, 1771, lambda c: True, only=[L1760]).candidates == [X1280, L1760]
    plan = plan_candidates(X1280, 965, 1771, lambda c: True, all_sizes=True, dancer_net_px=78.0)
    assert set(plan.candidates) == {X1280, X1760, X1920, L1280, L1760, L1920}


# ----------------------------------------------------------------------------------------------------
# the tap (FrameProcessor)
# ----------------------------------------------------------------------------------------------------
def _proc():
    pytest.importorskip("torch")
    from core.pipeline import FrameProcessor, ProcessingSettings
    proc = FrameProcessor(model=None, settings=ProcessingSettings(
        confidence=0.3, imgsz=1280, use_fp16=False, enhance_enabled=False, enhance_lite=False,
        enhance_force=False, person_height_px=200, use_gpu_path=False))
    proc.settings.swipe_collect = True
    clock = {"t": 0.0}
    proc.set_output_clock(lambda: clock["t"])
    return proc, clock


def _fg(*groups, valid=True):
    from core.foreground import FgBlob, FgFrame
    blobs = [FgBlob(x=x, y=y, area=a, mass=a, bbox=(x - 10, y - 10, x + 10, y + 10)) for x, y, a in groups]
    return FgFrame(np.zeros((8, 8), np.uint8) if valid else None, 0.0, 0.0, 4, blobs=blobs, valid=valid)


def _tap(proc, frame, fg, boxes=(), mono=False):
    before = proc.last_fg
    proc.last_fg = fg
    proc.last_raw_boxes = list(boxes)
    timing = {}
    proc._swipe_tap(frame, before, mono, timing)
    return timing


def test_raw_boxes_are_original_pixels_with_their_confidence():
    from core.pipeline import FrameProcessor
    det = (None, None, np.array([110.0, 220.0, 40.0, 80.0]))
    confs = {FrameProcessor._bbox_conf_key(det[2]): 0.42}
    (x0, y0, x1, y1, c), = FrameProcessor._raw_boxes([det], confs, 2.0, 10.0, 20.0, 135.0, 3.0)
    assert (x0, y0, x1, y1) == (135 + 200, 3 + 400, 135 + 200 + 80, 3 + 400 + 160) and c == 0.42


def test_the_tap_keeps_a_copy_of_a_lost_frame_and_its_live_boxes():
    proc, clock = _proc()
    frame = np.full((40, 30, 3), 7, np.uint8)
    t = _tap(proc, frame, _fg((15, 20, 900)), boxes=[(0, 0, 5, 5, 0.9)])     # the box is on nobody
    assert "swipe_tap" in t
    frames, lost, prog, ready = proc.swipe_snapshot()
    assert lost == 1 and len(frames) == 1 and frames[0].lost
    assert frames[0].image is not frame and np.array_equal(frames[0].image, frame)
    frame[:] = 0                                                    # the camera reuses its buffer
    assert frames[0].image[0, 0, 0] == 7


def test_the_tap_keeps_one_channel_of_a_mono_ids_frame():
    proc, clock = _proc()
    frame = np.dstack([np.arange(1200, dtype=np.uint8).reshape(40, 30)] * 3)
    _tap(proc, frame, _fg((15, 20, 900)), mono=True)
    img = proc.swipe_snapshot()[0][0].image
    assert img.shape == (40, 30) and np.array_equal(img, frame[:, :, 0])


def test_the_tap_skips_stale_invalid_and_empty_frames():
    proc, clock = _proc()
    frame = np.zeros((40, 30, 3), np.uint8)
    fg = _fg((15, 20, 900))
    _tap(proc, frame, fg)
    assert proc.swipe_snapshot()[1] == 1
    clock["t"] = 5.0
    proc._swipe_tap(frame, fg, False, {})               # the same FgFrame object: the slot layer is off
    _tap(proc, frame, _fg((15, 20, 900), valid=False))  # plate stale / other crop
    _tap(proc, frame, _fg((15, 20, 50)))                 # too small to be a dancer
    assert proc.swipe_snapshot()[1] == 1
    _tap(proc, frame, _fg((15, 20, 900)))
    assert proc.swipe_snapshot()[1] == 2


class _CountingFrame(np.ndarray):
    """A camera buffer that counts its copies."""
    copies = 0

    def copy(self, *a, **k):
        _CountingFrame.copies += 1
        return np.asarray(self).copy()


def test_a_refused_frame_is_never_copied():
    """Every frame with a dancer is offered (the collector counts how often the live setting loses one); the
    image goes as a callable the collector calls only when it keeps the frame: a frame it refuses (the gap, a
    full pool when it refuses) never costs a copy -- copies == frames kept, whatever core.swipe's policy."""
    from core.swipe import FrameCollector
    proc, clock = _proc()
    offers = []

    class Spy(FrameCollector):
        def offer(self, t, image, obs, boxes):
            kept = super().offer(t, image, obs, boxes)
            offers.append((callable(image), kept))
            return kept
    proc._swipe_collector = Spy(SwipeParams(frames=3))
    frame = np.zeros((40, 30, 3), np.uint8).view(_CountingFrame)
    _CountingFrame.copies = 0
    for i in range(3):                                             # fills the lost pool: 3 copies
        clock["t"] = float(i)
        _tap(proc, frame, _fg((15, 20, 900)))
    assert proc.swipe_snapshot()[3] and _CountingFrame.copies == 3
    for i in range(40):                                            # pool full, a bare dancer on every frame
        clock["t"] = 10.0 + 0.05 * i
        _tap(proc, frame, _fg((15, 20, 900)))
    kept = sum(k for _f, k in offers)
    assert len(offers) == 43 and all(is_fn for is_fn, _k in offers)
    assert _CountingFrame.copies == kept and kept < 10            # 40 offers at 20 fps: a handful kept


def test_a_mono_frame_is_extracted_only_when_kept(monkeypatch):
    import core.pipeline as pl
    proc, clock = _proc()
    calls = []
    real = pl.cv2.extractChannel
    monkeypatch.setattr(pl.cv2, "extractChannel", lambda a, c: calls.append(c) or real(a, c))
    frame = np.dstack([np.full((40, 30), 9, np.uint8)] * 3)
    _tap(proc, frame, _fg((15, 20, 900)), mono=True)              # kept
    clock["t"] = 0.1
    _tap(proc, frame, _fg((15, 20, 900)), mono=True)              # inside the gap: refused
    assert calls == [0]


def test_a_covered_dancer_is_filler_and_a_setting_change_starts_over():
    proc, clock = _proc()
    frame = np.zeros((40, 30, 3), np.uint8)
    _tap(proc, frame, _fg((15, 20, 900)), boxes=[(5, 5, 25, 35, 0.8)])
    frames, lost, _p, _r = proc.swipe_snapshot()
    assert lost == 0 and len(frames) == 1 and not frames[0].lost
    proc.settings.yolo_shape = (1760, 960)               # the live setting changed: start over
    clock["t"] = 1.0
    _tap(proc, frame, _fg((15, 20, 900)))
    frames, lost, _p, _r = proc.swipe_snapshot()
    assert lost == 1 and len(frames) == 1
    proc.reset_swipe_frames()
    assert proc.swipe_snapshot()[:2] == ([], 0)


def test_the_snapshot_only_returns_frames_of_the_current_setting_and_age():
    """Frames of another project / setting (or older than max_age_s) are never handed to a swipe, even when
    no newer frame has been offered since (STANDBY after a project switch)."""
    proc, clock = _proc()
    frame = np.zeros((40, 30, 3), np.uint8)
    for i in range(3):
        clock["t"] = float(i)
        _tap(proc, frame, _fg((15, 20, 900)))
    assert proc.swipe_snapshot()[1] == 3
    proc.settings.roi_enabled, proc.settings.roi_w = True, 20          # another ROI, no frame offered since
    assert proc.swipe_snapshot() == ([], 0, 0.0, False)
    proc.settings.roi_enabled, proc.settings.roi_w = False, 0
    assert proc.swipe_snapshot()[1] == 3
    max_age = getattr(proc.swipe_collector.params, "max_age_s", None) or 300.0
    clock["t"] = 1.5 + max_age                                         # frames 0 and 1 are too old now
    frames, lost, _p, ready = proc.swipe_snapshot()
    assert [f.t for f in frames] == [2.0] and lost == 1 and not ready


def test_a_failing_tap_never_raises(capsys):
    proc, clock = _proc()
    _tap(proc, object(), _fg((15, 20, 900)))             # not an array: the copy fails
    assert "[Swipe] frame tap failed" in capsys.readouterr().out


# ----------------------------------------------------------------------------------------------------
# the runner: letterbox there and back, square and rectangles (CPU torch, a fake model)
# ----------------------------------------------------------------------------------------------------
class _Boxes:
    def __init__(self, xyxy, conf):
        import torch
        self.xyxy = torch.tensor(xyxy, dtype=torch.float32).reshape(-1, 4)
        self.conf = torch.tensor(conf, dtype=torch.float32).reshape(-1)

    def __len__(self):
        return len(self.conf)


class _BrightBoxModel:
    """Finds the bright rectangle in the network input and reports its box (network px), conf 0.8."""

    def __init__(self):
        self.shapes = []
        self.calls = 0

    def __call__(self, t, imgsz=None, conf=None, iou=None, half=None, verbose=False):
        import torch
        self.calls += 1
        self.shapes.append(tuple(t.shape))
        g = t[0].mean(0)
        ys, xs = torch.nonzero(g > 0.9, as_tuple=True)
        if len(xs) == 0:
            return [SimpleNamespace(boxes=_Boxes([], []))]
        box = [float(xs.min()), float(ys.min()), float(xs.max()) + 1.0, float(ys.max()) + 1.0]
        return [SimpleNamespace(boxes=_Boxes([box], [0.8]))]


def _facade_frame(mono=False):
    img = np.full((1824, 1260), 40, np.uint8)
    img[600:900, 400:520] = 255                     # the « dancer »: x 400-520, y 600-900 (original px)
    return img if mono else np.dstack([img] * 3)


def _runner(model):
    torch = pytest.importorskip("torch")
    pytest.importorskip("kornia")
    from core.swipe_runner import CandidateRunner
    x, y, w, h = FACADE_ROI
    inp = SwipeInput(roi_enabled=True, roi_x=x, roi_y=y, roi_w=w, roi_h=h, half=False)
    return CandidateRunner(inp, device="cpu", loader=lambda path: model)


@pytest.mark.parametrize("cand", [X1280, L1760, L1920], ids=lambda c: c.label)
def test_letterbox_there_and_back(cand):
    model = _BrightBoxModel()
    runner = _runner(model)
    run = runner.run([_facade_frame(), _facade_frame(mono=True)], cand, "fake.engine")
    th, tw = cand.shape or (cand.imgsz, cand.imgsz)
    assert not run.error and run.frames == 2 and len(run.ms) == 2 and run.load_s >= 0.0
    assert set(model.shapes) == {(1, 3, th, tw)}                   # warm-up + frames at the candidate's input
    for boxes in run.boxes:
        (x0, y0, x1, y1, c), = boxes
        assert c == pytest.approx(0.8)
        assert abs(x0 - 400) < 3 and abs(y0 - 600) < 3 and abs(x1 - 520) < 3 and abs(y1 - 900) < 3


def test_the_runner_reproduces_the_live_gpu_pipeline_input(monkeypatch):
    """The prepared tensor equals what the live GpuPipeline.process hands YOLO (same crop, enhancement,
    letterbox), enhancement on -- run on the CPU (the live upload's device and pinned buffer patched)."""
    torch = pytest.importorskip("torch")
    pytest.importorskip("kornia")
    import core.gpu_pipeline as gp
    from core.swipe_runner import CandidateRunner
    monkeypatch.setattr(gp, "DEVICE", torch.device("cpu"))
    monkeypatch.setattr(torch.Tensor, "pin_memory", lambda self, *a, **k: self)
    x, y, w, h = FACADE_ROI
    inp = SwipeInput(roi_enabled=True, roi_x=x, roi_y=y, roi_w=w, roi_h=h, enhance_enabled=True,
                     enhance_force=True, clahe_clip=2.5, gamma=1.2, half=False)
    runner = CandidateRunner(inp, device="cpu", loader=lambda p: None)
    frame = _facade_frame()
    for cand in (X1280, L1920):
        live = gp.GpuPipeline(gp.GpuPipelineSettings(
            enhance_enabled=True, enhance_force=True, clahe_clip=2.5, gamma=1.2, roi_enabled=True,
            roi_x=x, roi_y=y, roi_w=w, roi_h=h, yolo_imgsz=cand.imgsz, yolo_shape=cand.shape))
        want, _prev, timing = live.process(frame, preview_enabled=False)
        got, lb, roi = runner.prepare(frame, cand)
        assert torch.equal(got, want)
        assert lb == timing["letterbox"] and roi == (x, y)


def test_a_candidate_that_cannot_load_is_skipped_and_cancel_stops_early():
    from core.swipe_runner import CandidateRunner
    pytest.importorskip("kornia")

    def broken(path):
        raise RuntimeError("serialization tag 243 vs 244")
    runner = CandidateRunner(SwipeInput(half=False), device="cpu", loader=broken)
    run = runner.run([_facade_frame()], X1280, "x.engine")
    assert run.error.startswith("load failed") and "243" in run.error and run.frames == 0
    model = _BrightBoxModel()
    runner = CandidateRunner(SwipeInput(half=False), device="cpu", loader=lambda p: model)
    n = {"k": 0}

    def cancelled():                     # after the loader, before frame 0, before frame 1 (-> stop)
        n["k"] += 1
        return n["k"] > 2
    run = runner.run([_facade_frame()] * 3, X1280, "x.engine", cancelled=cancelled)
    assert run.error == "cancelled" and run.frames == 1


def test_a_cancel_after_the_loader_skips_the_warm_up(monkeypatch):
    """A live load waiting for the engine lock waits one engine load at most: the engine deserialises at the
    warm-up, which a cancel seen right after the loader returns skips."""
    pytest.importorskip("kornia")
    from core.swipe_runner import CandidateRunner
    model = _BrightBoxModel()
    runner = CandidateRunner(SwipeInput(half=False), device="cpu", loader=lambda p: model)
    run = runner.run([_facade_frame()], X1280, "x.engine", cancelled=lambda: True)
    assert run.error == "cancelled" and model.calls == 0


def test_candidates_are_dropped_without_a_collection_and_finalized_once(monkeypatch):
    """No full gc.collect per candidate (it holds the GIL ~90 ms); one collection and one empty_cache at the
    end of the swipe, after a live frame."""
    pytest.importorskip("kornia")
    import gc as gcmod
    import core.swipe_runner as sr
    from core.swipe_runner import CandidateRunner
    collects = []
    monkeypatch.setattr(sr.gc, "collect", lambda *a: collects.append(a) or 0)
    paces = []
    runner = CandidateRunner(SwipeInput(half=False), device="cpu", loader=lambda p: _BrightBoxModel())
    frames = [_facade_frame()] * 2
    obs = [Observation(groups=[(460.0, 750.0, 5000.0)])] * 2
    out = run_swipe(frames, obs, CandidatePlan([X1280, L1920]), X1280, runner, lambda c: c.engine_file,
                    SwipeParams(min_groups=1), pace=lambda: paces.append(1))
    assert len(collects) == 1 and out.result is not None and "release_s" in out.timing
    assert len(paces) >= 2 * 2 + 2 + 1          # each inference, each drop, the final collection
    assert gcmod.collect is not None


def test_a_cancelled_swipe_makes_no_cuda_call_at_its_end():
    """Dropped because the live model reloads: its first RUN frame captures a graph outside any lock, so the
    end of a cancelled swipe calls no empty_cache (the engines are already freed, under the lock)."""
    calls = []

    class _R(_FakeRunner):
        def finalize(self, pace=None, empty_cache=True):
            calls.append(empty_cache)
            return {}
    frames = _frames()
    n = {"k": 0}

    def cancelled():
        n["k"] += 1
        return n["k"] > 1
    out = run_swipe([f.image for f in frames], [f.obs for f in frames], CandidatePlan([X1280, L1920]), X1280,
                    _R(), lambda c: c.engine_file, SwipeParams(), cancelled=cancelled)
    assert out.cancelled and out.result is None and calls == [False]
    out = run_swipe([f.image for f in frames], [f.obs for f in frames], CandidatePlan([X1280, L1920]), X1280,
                    _R(), lambda c: c.engine_file, SwipeParams())
    assert out.result is not None and calls == [False, True]


def test_the_runner_never_touches_the_live_background_subtractor():
    torch = pytest.importorskip("torch")
    pytest.importorskip("kornia")
    from core.background import BackgroundSubtractor
    from core.swipe_runner import CandidateRunner
    live = BackgroundSubtractor()
    live._ref_gpu = torch.zeros((1, 3, 1824, 1260))
    live.foreground_ratio, live.is_mismatched = 0.01, False
    runner = CandidateRunner(SwipeInput(bg_subtract=True, half=False), device="cpu",
                             loader=lambda p: _BrightBoxModel(), bg_subtractor=live)
    assert runner._bg is not live and runner._bg._ref_gpu is live._ref_gpu
    runner.prepare(_facade_frame(), X1280)
    assert live.foreground_ratio == 0.01 and runner._bg.foreground_ratio > 0.01


def test_the_swipe_thread_captures_no_cuda_graph_and_holds_the_engine_lock():
    """A TensorRT load captures a CUDA graph that another thread's CUDA work breaks (measured 26/11): in the
    swipe's thread ``torch.cuda.graph`` captures nothing (and the backend keeps no graph), other threads are
    untouched; each candidate's run holds the process-wide engine-load lock, as ModelManager.load_model does."""
    torch = pytest.importorskip("torch")
    pytest.importorskip("kornia")
    from core import model_manager as mmod
    from core.swipe_runner import CandidateRunner, _NoCapture, install_capture_guards, no_graph_capture
    assert install_capture_guards() and install_capture_guards()          # idempotent
    assert getattr(torch.cuda.graph, "_wd_guarded", False)
    from ultralytics.nn.backends.tensorrt import TensorRTBackend
    assert getattr(TensorRTBackend.load_model, "_wd_wrapped", False)
    with no_graph_capture():
        assert isinstance(torch.cuda.graph(object()), _NoCapture)
        seen = {}
        t = threading.Thread(target=lambda: seen.update(other=getattr(
            __import__("core.swipe_runner", fromlist=["_tls"])._tls, "no_capture", False)))
        t.start()
        t.join()
        assert seen["other"] is False                                     # thread-local
    owned = []

    class _LockProbe(_BrightBoxModel):
        def __call__(self, *a, **k):
            owned.append(mmod.ENGINE_LOAD_LOCK._is_owned())
            return super().__call__(*a, **k)
    runner = CandidateRunner(SwipeInput(half=False), device="cpu", loader=lambda p: _LockProbe())
    runner.run([_facade_frame()], X1280, "x.engine")
    assert owned and all(owned)
    calls = []
    mm = mmod.ModelManager.__new__(mmod.ModelManager)
    mm._load_model = lambda name, force_pt=False: calls.append(mmod.ENGINE_LOAD_LOCK._is_owned())
    mm.load_model("yolo11x-pose")
    assert calls == [True]


def test_unletterbox_matches_the_letterbox():
    lb = {"scale": 0.5, "pad_x": 12, "pad_y": 0}
    a = unletterbox_xyxy(np.array([[12, 0, 62, 100]]), lb, 135, 3)
    assert a.tolist() == [[135, 3, 235, 203]]
    boxes = type("B", (), {"xyxy": np.array([[12.0, 0, 62, 100]]), "conf": np.array([0.3]),
                           "__len__": lambda self: 1})()
    res = [SimpleNamespace(boxes=boxes)]
    assert boxes_from_results(res, lb, 135, 3) == [(135.0, 3.0, 235.0, 203.0, pytest.approx(0.3))]


# ----------------------------------------------------------------------------------------------------
# the whole swipe with a fake runner, and the controller
# ----------------------------------------------------------------------------------------------------
DANCER = (500.0, 500.0, 1000.0)
ON_HER = (450.0, 400.0, 550.0, 600.0, 0.6)


class _FakeRunner:
    """l candidates see the dancer, x candidates do not; ``on_run`` hooks a test into a run."""

    def __init__(self, on_run=None):
        self.calls = []
        self.on_run = on_run

    def run(self, images, cand, engine_path, *, cancelled=None, pace=None, on_frame=None):
        self.calls.append(cand)
        if self.on_run is not None:
            self.on_run(cand)
        run = CandidateRun(cand, load_s=0.1)
        for _ in images:
            if cancelled is not None and cancelled():
                run.error = "cancelled"
                return run
            if pace is not None:
                pace()
            run.boxes.append([ON_HER] if "11l" in cand.model else [])
            run.ms.append(5.0)
        return run


def _frames(n=30):
    from core.swipe import SwipeFrame
    return [SwipeFrame(float(i), np.zeros((1824, 1260), np.uint8), Observation(groups=[DANCER]), True)
            for i in range(n)]


def test_run_swipe_scores_every_candidate_and_names_the_winner():
    frames = _frames()
    plan = CandidatePlan([X1280, L1920, L1760])
    out = run_swipe([f.image for f in frames], [f.obs for f in frames], plan, X1280, _FakeRunner(),
                    lambda c: c.engine_file, SwipeParams(), lost_frames=30)
    assert out.result.switch and out.result.winner in (L1920, L1760)
    d = out.to_dict()
    json.dumps(d)
    assert d["current"] == "x @ 1280" and d["switch"] and d["frames"] == 30
    assert d["rows"][0]["coverage"] == 1.0 and d["rows"][0]["load_s"] == 0.1 and d["rows"][0]["frames_run"] == 30
    assert set(d["timing"]) >= {"load_s", "infer_s", "run_s"}


def _exclusion_proc():
    from core.pipeline import FrameProcessor
    proc, clock = _proc()
    proc.set_exclusion((16, 10), [(3, 4)])                          # one auto cell (col 3, row 4)
    proc.settings.roi_enabled, proc.settings.roi_x, proc.settings.roi_y = True, 135, 3
    proc.settings.roi_w, proc.settings.roi_h = 965, 1771
    det = proc.crossval_motion_detector
    det._clean_mask = np.zeros((int(1771 * det._scale), int(965 * det._scale)), np.uint8)
    return proc, clock, det


def test_the_excluded_cells_match_the_pipelines_own_test():
    """Same cell for the same box as FrameProcessor._apply_exclusion's transform (letterboxed tracker space)."""
    proc, clock, det = _exclusion_proc()
    from core.swipe_runner import ExclusionCells
    cells = ExclusionCells.from_processor(proc, (135, 3, 965, 1771))
    assert cells is not None and cells.cells == frozenset({(3, 4)})
    scale, pad_x, pad_y = 1280 / 1771.0, (1280 - int(965 * 1280 / 1771.0)) // 2, 0
    for ox in range(0, 965, 37):
        for oy in range(0, 1771, 53):
            cx, cy = ox * scale + pad_x, oy * scale + pad_y            # letterbox space, ROI-local
            nxy = proc._exclusion_norm_xy(cx, cy, det, scale, pad_x, pad_y, 135, 3, True)
            want = nxy is not None and proc._exclusion.excluded(*nxy)
            assert cells.excluded(135 + ox, 3 + oy) == want


def test_a_box_in_an_excluded_cell_is_not_a_box_on_nobody():
    """The pipeline drops it before tracking: harmless in the show, so the referee never sees it."""
    proc, clock, det = _exclusion_proc()
    from core.swipe_runner import ExclusionCells
    cells = ExclusionCells.from_processor(proc, (135, 3, 965, 1771))
    cx, cy = 135 + 3.5 * 965 / 16.0, 3 + 4.5 * 1771 / 10.0         # the middle of cell (3, 4)
    ghost = (cx - 30, cy - 60, cx + 30, cy + 60, 0.5)
    mask = np.zeros((443, 242), np.uint8)
    mask[200:260, 120:140] = 1                                       # the dancer's foreground only
    dancer = (135 + 130 * 4.0, 3 + 230 * 4.0, 1000.0)
    on_her = (dancer[0] - 40, dancer[1] - 100, dancer[0] + 40, dancer[1] + 100, 0.7)
    obs = Observation(groups=[dancer], mask=mask, x0=135.0, y0=3.0, ds=4)

    class _Ghost(_FakeRunner):
        def run(self, images, cand, engine_path, **kw):
            r = CandidateRun(cand)
            r.boxes = [[on_her, ghost]] * len(images)
            r.ms = [5.0] * len(images)
            return r
    plan = CandidatePlan([X1280])
    imgs = [np.zeros((4, 4), np.uint8)] * 25
    plain = run_swipe(imgs, [obs] * 25, plan, X1280, _Ghost(), lambda c: c.engine_file, SwipeParams())
    masked = run_swipe(imgs, [obs] * 25, plan, X1280, _Ghost(), lambda c: c.engine_file, SwipeParams(),
                       exclusion=cells)
    assert plain.result.rows[0].loose_per_frame > 0.9
    assert masked.result.rows[0].loose_per_frame == 0.0


def test_the_taps_live_boxes_are_read_after_the_exclusion():
    """« lost » means lost after the operator's excluded cells: a box there does not cover the dancer."""
    proc, clock, det = _exclusion_proc()
    cx, cy = 135 + 3.5 * 965 / 16.0, 3 + 4.5 * 1771 / 10.0
    frame = np.zeros((1824, 1260, 3), np.uint8)
    _tap(proc, frame, _fg((cx, cy, 900)), boxes=[(cx - 30, cy - 60, cx + 30, cy + 60, 0.8)])
    frames, lost, _p, _r = proc.swipe_snapshot()
    assert lost == 1 and frames[0].lost and frames[0].boxes == ()
    proc.set_exclusion((16, 10), [])
    clock["t"] = 5.0
    _tap(proc, frame, _fg((cx, cy, 900)), boxes=[(cx - 30, cy - 60, cx + 30, cy + 60, 0.8)])
    frames, lost, _p, _r = proc.swipe_snapshot()
    assert lost == 1 and not frames[-1].lost


def test_a_failed_live_re_run_gives_no_verdict():
    """Nothing to compare the others with: no result (it would read 0 % for the live setting and offer
    Apply)."""
    class _LiveFails(_FakeRunner):
        def run(self, images, cand, engine_path, **kw):
            if cand == X1280:
                return CandidateRun(cand, error="load failed: RuntimeError: tag 243")
            return super().run(images, cand, engine_path, **kw)
    frames = _frames()
    out = run_swipe([f.image for f in frames], [f.obs for f in frames], CandidatePlan([X1280, L1920]), X1280,
                    _LiveFails(), lambda c: c.engine_file, SwipeParams())
    assert out.result is None and out.live_error.startswith("load failed")
    assert len(out.runs) == 1                         # the others are not even tried


class _FakeProc:
    def __init__(self, frames, lost, ready=True):
        from core.pipeline import ProcessingSettings
        self.frames, self.lost, self.ready = frames, lost, ready
        self.settings = ProcessingSettings(confidence=0.15, imgsz=1280, use_fp16=True, enhance_enabled=False,
                                           enhance_lite=False, enhance_force=False, person_height_px=150,
                                           roi_enabled=True, roi_x=135, roi_y=3, roi_w=965, roi_h=1771)
        self.enhancer = SimpleNamespace(clahe_clip=2.5, gamma=1.2)
        self.bg_subtractor = None
        self.frame_done = threading.Event()
        self.frame_done_t = 0.0
        self.dancer_height = None
        self.last_lb_scale = 0.0

    def swipe_snapshot(self):
        return list(self.frames), self.lost, min(1.0, self.lost / 30.0), self.ready


def _controller(proc, live, runner, **kw):
    from runtime.swipe_controller import SwipeController
    events = {"progress": [], "result": []}
    plate = kw.pop("plate", True)
    kw.setdefault("pace", False)                       # pacing has its own test
    c = SwipeController(processor=lambda: proc, live=lambda: live["v"], engine_path=lambda c: c.engine_file,
                        plate_ready=lambda: plate,
                        on_progress=lambda s, t, p: events["progress"].append((s, t, p)),
                        on_result=lambda r, e: events["result"].append((r, e)),
                        runner_factory=lambda inp, p: runner, engine_exists=lambda path: True,
                        poll_s=0.01, **kw)
    return c, events


def _live(cand=X1280, project="jeudi0809_mur2", model_id=1, trt=True, roi=(True, 135, 3, 965, 1771),
          plate="plates/plate_20261008_213618.npz", plate_ok=True, frame=(1260, 1824)):
    from runtime.swipe_controller import LiveSetting
    return LiveSetting(project, cand, model_id, trt, roi=roi, plate=plate, plate_ok=plate_ok, frame=frame)


def test_the_controller_collects_runs_and_reports_a_switch():
    proc = _FakeProc(_frames(), 30)
    live = {"v": _live()}
    c, ev = _controller(proc, live, _FakeRunner())
    assert c.start() is None
    c.join(5)
    assert c.state == "done"
    states = [s for s, _t, _p in ev["progress"]]
    assert states[0] == "collecting" and "running" in states and states[-1] == "done"
    assert any("trying l @ 1920 x 1056" in t for _s, t, _p in ev["progress"])
    (res, err), = ev["result"]
    assert err == "" and res["switch"] and res["winner"].startswith("l @")
    row = c.readiness(plate_ready=True, live_now=live["v"])
    assert row.status == "warn" and "covers 100 % of the dancers, x @ 1280 0 %" in row.detail
    assert "Apply" in row.detail
    why, winner, conf = c.applicable(live["v"])
    assert why is None and winner.model == "yolo11l-pose"
    assert conf == getattr(c.outcome.result, "recommended_conf", None)
    assert c.status_dict()["switch"]


def test_the_controller_waits_for_lost_frames_and_can_be_cancelled():
    proc = _FakeProc([], 0, ready=False)
    live = {"v": _live()}
    c, ev = _controller(proc, live, _FakeRunner())
    c.start()
    time.sleep(0.05)
    assert c.busy and c.state == "collecting" and f"collecting 0 / {SwipeParams().frames}" in c.text
    assert c.start() == "a swipe is already running"
    c.cancel()
    c.join(5)
    assert c.state == "cancelled" and c.outcome is None
    assert ev["result"][-1][1].startswith("swipe cancelled")
    assert "no swipe yet (last one cancelled" in c.readiness(plate_ready=True, live_now=live["v"]).detail


def test_a_project_switch_mid_swipe_drops_the_result():
    proc = _FakeProc(_frames(), 30)
    live = {"v": _live()}

    def switch(cand):
        if cand == L1920:
            live["v"] = _live(project="hangar")
    c, ev = _controller(proc, live, _FakeRunner(on_run=switch))
    c.start()
    c.join(5)
    assert c.state == "dropped" and c.outcome is None and "project switched to hangar" in c.text
    assert ev["result"][-1] == ({}, "swipe dropped: project switched to hangar")


def test_a_model_change_mid_swipe_drops_the_result():
    proc = _FakeProc(_frames(), 30)
    live = {"v": _live()}

    def reload(cand):
        live["v"] = _live(model_id=2)                    # the live model object was reloaded
    c, ev = _controller(proc, live, _FakeRunner(on_run=reload))
    c.start()
    c.join(5)
    assert c.state == "dropped" and "live setting changed" in c.text


def test_a_scene_change_mid_swipe_drops_it_and_stales_a_verdict():
    """The ROI, the plate (or its validity) and the camera frame are part of what a verdict was measured on."""
    proc = _FakeProc(_frames(), 30)
    live = {"v": _live()}

    def move_roi(cand):
        live["v"] = _live(roi=(True, 100, 3, 1000, 1771))
    c, ev = _controller(proc, live, _FakeRunner(on_run=move_roi))
    c.start()
    c.join(5)
    assert c.state == "dropped" and c.text == "the ROI changed"
    live["v"] = _live()
    c, ev = _controller(proc, live, _FakeRunner())
    c.start()
    c.join(5)
    assert c.state == "done"
    for change, words in ((dict(plate="plates/latest.npz"), "the empty-wall plate changed"),
                          (dict(plate_ok=False), "the empty-wall plate changed"),
                          (dict(frame=(1920, 1080)), "the camera frame size changed")):
        now = _live(**change)
        row = c.readiness(plate_ready=True, live_now=now)
        assert row.status == "skip" and row.detail.startswith(words + " since the last swipe")
        assert c.applicable(now)[0].startswith(words)


def test_a_failed_live_re_run_ends_the_swipe_in_error():
    class _LiveFails(_FakeRunner):
        def run(self, images, cand, engine_path, **kw):
            return CandidateRun(cand, error="inference failed: AcceleratorError") if cand == X1280 \
                else super().run(images, cand, engine_path, **kw)
    proc = _FakeProc(_frames(), 30)
    c, ev = _controller(proc, {"v": _live()}, _LiveFails())
    c.start()
    c.join(5)
    assert c.state == "error" and c.outcome is None
    assert c.text == "the live setting could not be re-run: inference failed: AcceleratorError"
    assert c.applicable(_live())[0].startswith("no swipe result")


def test_a_run_just_after_an_end_is_refused():
    """A Cancel click landing as the swipe ends must not start a new one."""
    proc = _FakeProc(_frames(), 30)
    c, ev = _controller(proc, {"v": _live()}, _FakeRunner())
    c.start()
    c.join(5)
    assert c.start() == "a swipe just ended - press again to start a new one"
    c.rerun_guard_s = 0.0
    assert c.start() is None
    c.join(5)


def test_the_controller_refuses_without_plate_or_tensorrt():
    proc = _FakeProc(_frames(), 30)
    c, ev = _controller(proc, {"v": _live()}, _FakeRunner(), plate=False)
    assert "empty-wall plate" in c.start() and ev["result"][-1][1]
    c, ev = _controller(proc, {"v": _live(trt=False)}, _FakeRunner())
    assert "TensorRT" in c.start()
    c, ev = _controller(proc, {"v": None}, _FakeRunner())
    assert c.start() == "no model loaded"


def test_after_a_timeout_covered_frames_fill_in():
    from core.swipe import SwipeFrame
    frames = [SwipeFrame(float(i), np.zeros((1824, 1260), np.uint8), Observation(groups=[DANCER]), i < 5)
              for i in range(30)]
    proc = _FakeProc(frames, 5, ready=False)
    c, ev = _controller(proc, {"v": _live()}, _FakeRunner(), collect_timeout_s=0.05)
    c.start()
    c.join(5)
    assert c.state == "done" and "only 5 frames with a dancer bare" in c.text


def test_pacing_waits_for_a_live_pass_and_the_interval():
    from runtime.swipe_controller import make_pacer
    proc = SimpleNamespace(frame_done=threading.Event(), frame_done_t=0.0)
    pace = make_pacer(proc, 0.05)
    t0 = time.perf_counter()
    pace()
    pace()                                             # the loop is idle: only the interval
    assert time.perf_counter() - t0 >= 0.05
    proc.frame_done_t = time.perf_counter()            # the loop runs YOLO, a pass just ended: go
    t1 = time.perf_counter()
    pace()
    assert time.perf_counter() - t1 < 0.1

    def live_pass():
        time.sleep(0.12)
        proc.frame_done_t = time.perf_counter()
        proc.frame_done.set()
    threading.Thread(target=live_pass).start()
    t2 = time.perf_counter()
    pace()                                             # no live pass since our last inference: wait for one
    assert time.perf_counter() - t2 >= 0.1


# ----------------------------------------------------------------------------------------------------
# readiness texts
# ----------------------------------------------------------------------------------------------------
def _result(cur, cov_cur, best, cov_best):
    def tally(cov):
        t = Tally()
        t.frames, t.groups, t.covered = 30, 100, round(100 * cov)
        return t
    tallies = {cur: tally(cov_cur)}
    if best is not None:
        tallies[best] = tally(cov_best)
    return decide(tallies, cur, SwipeParams())


def test_readiness_row_in_plain_words():
    r = check_swipe(plate_ready=False)
    assert r.status == "skip" and r.detail.startswith("no empty-wall plate")
    r = check_swipe(plate_ready=True)
    assert r.status == "skip" and r.detail.startswith("no swipe yet")
    r = check_swipe(plate_ready=True, state="collecting", text="collecting 12 / 30 frames where a dancer has no box")
    assert r.status == "skip" and "12 / 30" in r.detail
    switch = _result(X1280, 0.51, L1920, 0.91)
    r = check_swipe(plate_ready=True, state="done", result=switch)
    assert r.status == "warn"
    assert r.detail.startswith("l @ 1920 x 1056 covers 91 % of the dancers, x @ 1280 51 %")
    assert "press Apply" in r.detail and "Save the project" in r.detail
    keep = _result(X1280, 0.85, L1920, 0.86)
    r = check_swipe(plate_ready=True, state="done", result=keep)
    assert r.status == "ok" and r.detail.startswith("keep x @ 1280: it covers 85 %")
    r = check_swipe(plate_ready=True, state="done", result=switch, applied=True)
    assert r.status == "ok" and r.detail.startswith("running l @ 1920 x 1056, the swipe's pick")
    r = check_swipe(plate_ready=True, state="done", result=switch, stale=True, live_label="l @ 1280")
    assert r.status == "skip" and "changed since the last swipe" in r.detail and "l @ 1280 now" in r.detail
    none = _result(X1280, 0.5, None, 0)
    none.winner, none.reason = None, "no verdict: 12 dancer groups seen, 20 needed"
    r = check_swipe(plate_ready=True, state="done", result=none)
    assert r.status == "skip" and r.detail.startswith("no verdict")


def test_only_the_recommended_confidence_is_read():
    """``conf`` / ``confidence`` are not the show's confidence (SwipeParams.conf is the referee's own)."""
    assert recommended_conf(SimpleNamespace(conf=0.15, confidence=0.15)) is None
    assert recommended_conf(SimpleNamespace(recommended_conf=0.22)) == 0.22
    assert recommended_conf(SimpleNamespace(recommended_conf=None)) is None


# ----------------------------------------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------------------------------------
def _model_controller(tmp_path, monkeypatch, engines=("yolo11l-pose_1920x1056.engine",)):
    from core import model_manager as mmod
    from core.model_manager import ModelManager
    from runtime.model_controller import ModelController
    monkeypatch.setattr(mmod, "is_tensorrt_available", lambda: True)
    monkeypatch.delenv("WD_ENGINE_DIR", raising=False)
    for e in engines:
        (tmp_path / e).write_bytes(b"e")
    mc = ModelController.__new__(ModelController)
    mc.model_manager = ModelManager(str(tmp_path), use_tensorrt=True, imgsz=1280)
    settings = SimpleNamespace(imgsz=1280, yolo_shape=None)
    mc.processor = lambda: SimpleNamespace(settings=settings)
    mc._model_loading = False
    mc._model_loaded = True
    mc._pending_model_switch = None
    mc._pending_trt_switch = None
    mc._setting_request = None
    return mc, settings


def test_apply_queues_the_model_switch_a_manual_change_takes(tmp_path, monkeypatch):
    mc, settings = _model_controller(tmp_path, monkeypatch)
    assert mc.request_setting("yolo11l-pose", 1280, (1920, 1056)) is None
    assert mc._pending_model_switch == "yolo11l-pose" and mc._pending_trt_switch is True
    assert mc._model_loading and not mc._model_loaded
    assert mc.model_manager.shape == (1920, 1056) and mc.model_manager.imgsz == 1280
    assert mc.model_manager.get_engine_path("yolo11l-pose").endswith("yolo11l-pose_1920x1056.engine")
    assert mc.request_setting("yolo11x-pose", 1280, None) == "a model load is already in progress"


def test_apply_refuses_a_missing_engine(tmp_path, monkeypatch):
    mc, settings = _model_controller(tmp_path, monkeypatch, engines=())
    assert mc.request_setting("yolo11l-pose", 1280, (1760, 960)) == "no engine yolo11l-pose_1760x960.engine"
    assert mc._pending_model_switch is None and mc.model_manager.shape is None


def test_a_failed_apply_load_restores_the_old_input(tmp_path, monkeypatch):
    mc, settings = _model_controller(tmp_path, monkeypatch)
    done = []
    mc.ui = SimpleNamespace(available=False)
    mc._update_trt_banner = lambda: None
    mc.model = object()
    assert mc.request_setting("yolo11l-pose", 1280, (1920, 1056), on_done=done.append) is None
    mc._load_model_with_progress = lambda name, force_pt=False: False
    assert mc._drain_pending_model_switch()
    assert done == [False] and mc.model_manager.shape is None and settings.imgsz == 1280
    assert mc._setting_request is None
    mc._model_loading = False
    assert mc.request_setting("yolo11l-pose", 1280, (1920, 1056), on_done=done.append) is None
    mc._load_model_with_progress = lambda name, force_pt=False: True
    mc._drain_pending_model_switch()
    assert done == [False, True] and mc.model_manager.shape == (1920, 1056)


def _apply_fixture(trt=True):
    pytest.importorskip("dearpygui")
    import app as appmod
    proc = _FakeProc(_frames(), 30)
    live = {"v": _live()}
    c, ev = _controller(proc, live, _FakeRunner())
    c.start()
    c.join(5)
    c.outcome.result.recommended_conf = 0.21
    calls = []
    pending = {}

    def request(m, i, s, on_done=None):
        calls.append(("switch", m, i, s))
        pending["done"] = on_done
    fake = SimpleNamespace(
        swipe=c, _swipe_live_setting=lambda: live["v"], _swipe_pending_apply=None,
        configs=SimpleNamespace(_current_project="jeudi0809_mur2"), settings=SimpleNamespace(imgsz=1280),
        models=SimpleNamespace(request_setting=request, current_model_name="yolo11x-pose",
                               model_manager=SimpleNamespace(is_using_tensorrt=lambda: trt),
                               ui=SimpleNamespace(sync_model_combo=lambda n: calls.append(("combo", n)))),
        _sync=lambda kind, name, value: calls.append((kind, name, value)),
        _swipe_set_confidence=lambda v: calls.append(("conf", v)),
        bus=SimpleNamespace(publish=lambda e: calls.append(e)))
    fake._swipe_apply_done = lambda ok: appmod.WallDanceApp._swipe_apply_done(fake, ok)
    appmod.WallDanceApp._cb_apply_settings_swipe(fake)
    return appmod, c, live, calls, pending, fake


def test_apply_switches_then_applies_the_confidence_once_the_winner_runs_on_tensorrt():
    from runtime import api
    appmod, c, live, calls, pending, fake = _apply_fixture()
    winner = c.outcome.result.winner
    assert ("switch", winner.model, winner.imgsz, winner.shape) in calls
    assert ("combo", winner.model) in calls and ("combo", "imgsz", "1280") in calls
    applying = [e for e in calls if isinstance(e, api.SettingsSwipeProgress)]
    assert applying[-1].state == "applying"                     # the GUI hides Apply on it
    assert not any(e == ("conf", 0.21) for e in calls) and c.applied is None   # nothing before the switch
    live["v"] = _live(cand=winner, model_id=3)                  # the main loop ran the switch
    pending["done"](True)
    assert ("conf", 0.21) in calls and c.applied == winner
    last = [e for e in calls if isinstance(e, api.SettingsSwipeProgress)][-1]
    assert last.state == "applied" and "Save the project" in last.text
    assert c.readiness(plate_ready=True, live_now=live["v"]).detail.startswith("running l @")


def test_a_pytorch_fallback_or_a_failed_load_is_not_the_swipes_pick():
    from runtime import api
    for trt, ok, words in ((False, True, "running PyTorch"), (True, False, "previous one is kept")):
        appmod, c, live, calls, pending, fake = _apply_fixture(trt=trt)
        if ok:
            live["v"] = _live(cand=c.outcome.result.winner, model_id=3)
        pending["done"](ok)
        assert not any(isinstance(e, tuple) and e[0] == "conf" for e in calls) and c.applied is None
        last = [e for e in calls if isinstance(e, api.SettingsSwipeProgress)][-1]
        assert last.state == "apply_failed" and words in last.text and "confidence is unchanged" in last.text


def test_a_recommended_confidence_becomes_the_dials_anchor():
    """The dial keeps its position; confidence and varThreshold follow the macro from the new seed, and the
    confidence slider is synced (what runs is what shows)."""
    pytest.importorskip("dearpygui")
    import app as appmod
    seen = []
    fake = SimpleNamespace(sensitivity=60.0, _sensitivity_conf_seed=0.30,
                           _cb_sensitivity_change=lambda v: seen.append((v, fake._sensitivity_conf_seed)))
    appmod.WallDanceApp._swipe_set_confidence(fake, 0.15)
    assert seen == [(60.0, 0.15)]


def test_remote_apply_is_heavy_and_run_is_control():
    from services.remote_api import POLICY
    assert POLICY["ApplySettingsSwipe"] == "heavy" and POLICY["LoadModel"] == "heavy"
    assert POLICY["RunSettingsSwipe"] == "control" and POLICY["CancelSettingsSwipe"] == "safe"


def test_readiness_rows_are_published_even_when_the_console_cannot_print_them(monkeypatch):
    """The laptop's console is cp1252: a character it cannot encode must not stop the rows."""
    pytest.importorskip("dearpygui")
    import builtins
    import app as appmod
    from core.ops_monitor import CheckResult, ReadinessReport
    from runtime import api
    real_print = builtins.print

    def cp1252_print(*a, **k):
        text = " ".join(str(x) for x in a)
        text.encode("cp1252")                              # raises like the laptop's console
        real_print(*a, **k)
    monkeypatch.setattr(builtins, "print", cp1252_print)
    events = []
    report = ReadinessReport([CheckResult("swipe", "ok", "keep x @ 1280 \u2192 it covers 85 %")])
    fake = SimpleNamespace(_build_readiness_report=lambda: report,
                           configs=SimpleNamespace(_current_project="p", _active_profile="show"),
                           bus=SimpleNamespace(publish=events.append))
    appmod.WallDanceApp._cb_check_readiness(fake)
    assert any(isinstance(e, api.ReadinessResult) for e in events)


def test_quit_cancels_and_joins_the_swipe():
    pytest.importorskip("dearpygui")
    import app as appmod
    calls = []
    fake = SimpleNamespace(swipe=SimpleNamespace(cancel=lambda: calls.append("cancel"),
                                                 join=lambda t: calls.append(("join", t))),
                           tracker=SimpleNamespace(logger=SimpleNamespace(close=lambda: None)),
                           recorder=SimpleNamespace(close=lambda: None), running=True)
    appmod.WallDanceApp._cb_quit(fake)
    assert calls == ["cancel", ("join", 2.0)] and fake.running is False


def test_the_live_setting_is_none_while_a_model_loads():
    """A running swipe stops when the app starts loading a model (no CUDA work beside its graph capture)."""
    pytest.importorskip("dearpygui")
    import app as appmod
    models = SimpleNamespace(model=object(), _model_loaded=True, _model_loading=True,
                             current_model_name="yolo11l-pose",
                             model_manager=SimpleNamespace(is_using_tensorrt=lambda: True))
    settings = SimpleNamespace(imgsz=1280, yolo_shape=(1920, 1056), roi_enabled=True, roi_x=135, roi_y=3,
                               roi_w=965, roi_h=1771, fg_plate_path="plates/latest.npz")
    fake = SimpleNamespace(models=models, settings=settings,
                           configs=SimpleNamespace(_current_project="jeudi0809_mur2"),
                           roi=SimpleNamespace(state=SimpleNamespace(source_size=(1260, 1824))),
                           processor=SimpleNamespace(plate_usable=True))
    assert appmod.WallDanceApp._swipe_live_setting(fake) is None
    models._model_loading = False
    live = appmod.WallDanceApp._swipe_live_setting(fake)
    assert live.candidate == L1920 and live.trt and live.model_id == id(models.model)
    assert live.roi == (True, 135, 3, 965, 1771) and live.plate == "plates/latest.npz" and live.plate_ok
    assert live.frame == (1260, 1824)
