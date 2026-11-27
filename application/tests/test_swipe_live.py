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


def test_plan_mid_height_is_the_small_rule_until_a_take_covers_it():
    """110-200 px: no take covers the band, so no « other model at the live input » (review 26/11, I6)."""
    plan = plan_candidates(X1280, 965, 1771, lambda c: True, dancer_net_px=150.0)
    assert plan.candidates == [X1280, X1920, L1920]
    assert "l @ 1280 not tried: the dancer is ~150 px tall there, under 200: the largest input is tried" \
        in plan.skipped_text()


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
    # a gray 3-channel frame is kept as its one channel (a copy: the camera reuses its buffer)
    assert frames[0].image.shape == (40, 30) and np.array_equal(frames[0].image, frame[..., 0])
    frame[:] = 0
    assert frames[0].image[0, 0] == 7


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
    frame[..., 2] = 7                                              # a colour frame: kept as it is (a copy)
    _CountingFrame.copies = 0
    for i in range(3):                                             # fills the pool: 3 copies
        clock["t"] = float(i)
        _tap(proc, frame, _fg((15, 20, 900)))
    assert proc.swipe_snapshot()[3] and _CountingFrame.copies == 3
    for i in range(40):                                            # pool full, 2 s of a dancer at 20 fps
        clock["t"] = 10.0 + 0.05 * i
        _tap(proc, frame, _fg((15, 20, 900)))
    kept = sum(k for _f, k in offers)
    assert len(offers) == 43 and all(is_fn for is_fn, _k in offers)
    assert _CountingFrame.copies == kept and kept < 10            # 40 offers at 20 fps: a handful kept


def test_a_plate_taken_again_starts_the_pool_over():
    """J1 (review of 0e7fbe5): a plate taken again lands in the same plates/latest.npz (and Calibrate holds it in
    memory until Save): the pool keys on the plate in memory, so frames refereed against the old plate go."""
    from core.foreground import CleanPlate
    proc, clock = _proc()
    proc.settings.fg_plate_path = "/x/plates/latest.npz"
    plate = lambda v: CleanPlate.from_frames([np.full((64, 64), v, np.uint8)] * 2)   # noqa: E731
    proc.set_clean_plate(plate(10))
    frame = np.dstack([np.full((40, 30), 9, np.uint8)] * 3)
    for i in range(5):
        clock["t"] = 1.1 * i
        _tap(proc, frame, _fg((15, 20, 900)))
    assert len(proc.swipe_snapshot()[0]) == 5
    proc.set_clean_plate(plate(200))                               # the empty wall taken again, same path
    assert proc.swipe_snapshot()[0] == []                          # not this plate's frames
    clock["t"] = 10.0
    _tap(proc, frame, _fg((15, 20, 900)))
    frames = proc.swipe_snapshot()[0]
    assert len(frames) == 1 and frames[0].t == 10.0


class _LatchingDetector:
    """The rehearsal build's foreground as the swipe reads it (F2): its plate and ``stale_latched`` -- found
    stale, it stays so until a new plate.  This branch's ForegroundDetector has no such flag: ``getattr``."""

    def __init__(self, created="2026-11-26T20:00:00"):
        self.plate = SimpleNamespace(created=created)
        self.stale_latched = False


def test_a_plate_found_stale_is_not_usable_and_starts_the_pool_over():
    """F2: once the plate is found stale, single frames can be valid again while it does not fit the wall: the
    swipe reads the latch through ``plate_usable`` (its LiveSetting.plate_ok) and the tap's key -- the frames
    kept before the latch go, and lifting it (a new plate) starts the pool over again."""
    from core.foreground import CleanPlate
    proc, clock = _proc()
    proc.set_clean_plate(CleanPlate.from_frames([np.full((64, 64), 10, np.uint8)] * 2))
    assert proc.plate_usable                                       # this branch's detector: the flag is inert
    det = _LatchingDetector()
    proc._fg_detector = det
    assert proc.plate_usable
    frame = np.dstack([np.full((40, 30), 9, np.uint8)] * 3)
    for i in range(3):
        clock["t"] = float(i)
        _tap(proc, frame, _fg((15, 20, 900)))
    assert len(proc.swipe_snapshot()[0]) == 3
    key = proc._swipe_setting_key()
    det.stale_latched = True                                       # the plate found stale: latched
    assert not proc.plate_usable and proc._swipe_setting_key() != key
    assert proc.swipe_snapshot()[0] == []                          # frames refereed against a plate that fit
    clock["t"] = 3.0
    _tap(proc, frame, _fg((15, 20, 900)))                          # a valid frame under the latch: a new pool
    assert [f.t for f in proc.swipe_snapshot()[0]] == [3.0]
    det.stale_latched = False                                      # without the latch: usable again
    assert proc.plate_usable and proc._swipe_setting_key() == key
    det.stale_latched = True                                       # as F2 does: it goes only with a new plate
    proc._fg_detector = _LatchingDetector("2026-11-26T20:05:00")   # the empty wall taken again: no latch
    assert proc.plate_usable and proc.swipe_snapshot()[0] == []
    clock["t"] = 4.0
    _tap(proc, frame, _fg((15, 20, 900)))
    assert [f.t for f in proc.swipe_snapshot()[0]] == [4.0]


def test_a_gray_three_channel_frame_is_kept_as_one_channel_checked_only_when_kept(monkeypatch):
    """A take of the IDS camera played in the app: R == G == B exactly -- the pool keeps one channel (a third of
    the RAM); a frame whose channels differ stays as it is; the check runs only on a frame the collector keeps."""
    import core.pipeline as pl
    from core.pipeline import one_channel_if_gray
    from core.swipe import FrameCollector
    proc, clock = _proc()
    proc._swipe_collector = FrameCollector(SwipeParams(frames=3))
    checks = []
    monkeypatch.setattr(pl, "one_channel_if_gray", lambda f: checks.append(1) or one_channel_if_gray(f))
    gray = np.dstack([np.arange(40 * 30, dtype=np.uint8).reshape(40, 30)] * 3)
    colour = gray.copy()
    colour[5, 7, 2] += 1                                           # one value off: a colour frame
    for i, frame in enumerate((gray, colour)):
        clock["t"] = 10.0 * i
        _tap(proc, frame, _fg((15, 20, 900)))
        for k in range(5):                                         # refused: within min_gap_s of the kept one
            clock["t"] = 10.0 * i + 0.05 * (k + 1)
            _tap(proc, frame, _fg((15, 20, 900)))
    kept = [f.image for f in proc.swipe_snapshot()[0]]
    assert len(checks) == 2 and len(kept) == 2
    assert kept[0].shape == (40, 30) and np.array_equal(kept[0], gray[..., 0])
    assert kept[1].shape == (40, 30, 3) and np.array_equal(kept[1], colour)


def test_the_one_channel_copy_gives_the_same_network_input_boxes_and_scores(monkeypatch):
    """From the one-channel copy the runner's network input is the 3-channel frame's bit for bit -- enhancement
    off, full or lite, both inputs -- and so the boxes and the referee's scores.  (The 3-channel frame's input is
    the live playback path's: P5's runner tests.)"""
    torch = pytest.importorskip("torch")
    pytest.importorskip("kornia")
    import core.gpu_pipeline as gp
    from core.pipeline import one_channel_if_gray
    from core.swipe_runner import CandidateRunner, boxes_from_results
    monkeypatch.setattr(gp, "DEVICE", torch.device("cpu"))
    rng = np.random.default_rng(7)
    img = rng.integers(25, 70, (1824, 1260)).astype(np.uint8)
    img[600:900, 400:520] = 255
    bgr = np.dstack([img] * 3)
    kept = one_channel_if_gray(bgr)
    assert kept.ndim == 2
    x, y, w, h = FACADE_ROI
    obs = Observation(groups=[(460.0, 750.0, 36000.0)])
    for enh in ({"enhance_enabled": False}, {"enhance_enabled": True, "enhance_force": True},
                {"enhance_enabled": True, "enhance_lite": True, "enhance_force": True}):
        inp = SwipeInput(roi_enabled=True, roi_x=x, roi_y=y, roi_w=w, roi_h=h, clahe_clip=2.5, gamma=1.2,
                         half=False, **enh)
        model = _BrightBoxModel()
        runner = CandidateRunner(inp, device="cpu", loader=lambda p: model)
        for cand in (X1280, L1920):
            t3, lb3, roi3 = runner.prepare(bgr, cand)
            t1, lb1, roi1 = runner.prepare(kept, cand)
            assert torch.equal(t3, t1) and lb3 == lb1 and roi3 == roi1
            b3 = boxes_from_results(model(t3), lb3, *roi3)
            b1 = boxes_from_results(model(t1), lb1, *roi1)
            assert b1 == b3 and b1
            assert score_frame(obs, b1, SwipeParams()) == score_frame(obs, b3, SwipeParams())


@pytest.mark.parametrize("enh", [{"enhance_enabled": False},
                                 {"enhance_enabled": True, "enhance_force": True},
                                 {"enhance_enabled": True, "enhance_lite": True, "enhance_force": True},
                                 {"enhance_enabled": True, "enhance_lite": True, "brightness_threshold": 200.0}])
def test_the_network_input_is_contiguous(enh, monkeypatch):
    """26/11: the lite enhancement of a 3-channel frame left the tensor strided, and a TensorRT engine without a
    CUDA graph reads its raw pointer -- no box at all.  ``prepare`` hands a contiguous tensor, whatever the
    enhancement (off, full, lite, the lite blend of a dark frame), from a 3-channel or a mono frame."""
    torch = pytest.importorskip("torch")
    pytest.importorskip("kornia")
    import core.gpu_pipeline as gp
    from core.swipe_runner import CandidateRunner
    monkeypatch.setattr(gp, "DEVICE", torch.device("cpu"))
    x, y, w, h = FACADE_ROI
    inp = SwipeInput(roi_enabled=True, roi_x=x, roi_y=y, roi_w=w, roi_h=h, clahe_clip=2.5, gamma=1.2,
                     half=False, **enh)
    runner = CandidateRunner(inp, device="cpu", loader=lambda p: None)
    for frame in (_facade_frame(), _facade_frame(mono=True)):
        for cand in (X1280, L1920):
            assert runner.prepare(frame, cand)[0].is_contiguous()


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


def test_the_runner_reproduces_the_live_ids_input_from_the_kept_mono_frame(monkeypatch):
    """The IDS path: the camera hands the pipeline a (1, 3, H, W) float tensor made from the mono frame
    (``IdsCamera._mono_to_gpu_bgr``: ``.float().mul_(1/255).clamp_(0, 1)``, expanded to 3 channels) and the tap
    keeps the frame's one channel (``cv2.extractChannel``): the runner's network input from that channel is the
    live ``GpuPipeline.process_gpu_tensor`` input, bit for bit, enhancement on."""
    torch = pytest.importorskip("torch")
    pytest.importorskip("kornia")
    import cv2
    import core.gpu_pipeline as gp
    from core.swipe_runner import CandidateRunner
    monkeypatch.setattr(gp, "DEVICE", torch.device("cpu"))
    x, y, w, h = FACADE_ROI
    inp = SwipeInput(roi_enabled=True, roi_x=x, roi_y=y, roi_w=w, roi_h=h, enhance_enabled=True,
                     enhance_force=True, clahe_clip=2.5, gamma=1.2, half=False)
    runner = CandidateRunner(inp, device="cpu", loader=lambda p: None)
    bgr = _facade_frame()                                            # mono expanded to BGR (R == G == B)
    kept = cv2.extractChannel(bgr, 0)                                # what the tap keeps on the IDS path
    mono = torch.from_numpy(kept).float().mul_(1.0 / 255.0).clamp_(0.0, 1.0)   # _mono_to_gpu_bgr, on the CPU
    camera = mono.unsqueeze(0).unsqueeze(0).expand(1, 3, -1, -1).contiguous()
    for cand in (X1280, L1920):
        live = gp.GpuPipeline(gp.GpuPipelineSettings(
            enhance_enabled=True, enhance_force=True, clahe_clip=2.5, gamma=1.2, roi_enabled=True,
            roi_x=x, roi_y=y, roi_w=w, roi_h=h, yolo_imgsz=cand.imgsz, yolo_shape=cand.shape))
        want, _prev, timing = live.process_gpu_tensor(camera, preview_enabled=False)
        got, lb, roi = runner.prepare(kept, cand)
        assert torch.equal(got, want) and lb == timing["letterbox"]


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


def _gc_policy_module(installed, request=None, policy=True):
    """A stand-in for the rehearsal build's ``core.gc_policy`` (not on this branch): ``POLICY.installed``
    and ``request(reason)``."""
    import types
    mod = types.ModuleType("core.gc_policy")
    asked = []
    if policy:
        mod.POLICY = SimpleNamespace(installed=installed)
    mod.request = request or asked.append
    return mod, asked


def _finalize_with(monkeypatch, gc_policy, empty_cache=True):
    """(finalize's dict, the collections it ran) with ``gc_policy`` as ``sys.modules["core.gc_policy"]``
    (None: no such module)."""
    import sys
    import core.swipe_runner as sr
    from core.swipe_runner import CandidateRunner
    collects = []
    monkeypatch.setattr(sr, "gc", SimpleNamespace(collect=lambda *a: collects.append(a) or 0))
    if gc_policy is None:
        monkeypatch.delitem(sys.modules, "core.gc_policy", raising=False)
    else:
        monkeypatch.setitem(sys.modules, "core.gc_policy", gc_policy)
    runner = CandidateRunner(SwipeInput(half=False), device="cpu", loader=lambda p: _BrightBoxModel())
    return runner.finalize(lambda: None, empty_cache=empty_cache), collects


def test_an_installed_gc_policy_gets_the_end_of_swipe_collection_as_a_request(monkeypatch):
    """The rehearsal build's gc policy installed (U3, #t-029: no full collection in RUN with a dancer on the
    wall): the swipe's end asks it for one (served at its next invisible moment) instead of a 90-177 ms
    collection right after a live frame.  The dict keeps its keys; nothing was collected: ``gc_s`` 0.0."""
    pytest.importorskip("kornia")
    mod, asked = _gc_policy_module(installed=True)
    fin, collects = _finalize_with(monkeypatch, mod)
    assert asked == ["swipe ended"] and collects == []
    assert set(fin) == {"gc_s", "empty_cache_s"} and fin["gc_s"] == 0.0
    fin, collects = _finalize_with(monkeypatch, mod, empty_cache=False)    # a cancelled swipe: the same
    assert asked == ["swipe ended"] * 2 and collects == []


@pytest.mark.parametrize("case", ["not installed", "no such module", "no POLICY", "request raises"])
def test_without_an_installed_gc_policy_the_swipe_end_collects_as_before(monkeypatch, case):
    """This branch (no ``core.gc_policy``), the replay drivers and the tests (the policy never installed),
    a module without its policy object, a request that raises (it never escapes: the collection runs as
    before): one ``gc.collect`` at the end of the swipe, as measured in tonight's gates."""
    pytest.importorskip("kornia")

    def boom(reason):
        raise RuntimeError("the policy cannot take it")
    mod = {"not installed": lambda: _gc_policy_module(installed=False)[0],
           "no such module": lambda: None,
           "no POLICY": lambda: _gc_policy_module(installed=True, policy=False)[0],
           "request raises": lambda: _gc_policy_module(installed=True, request=boom)[0]}[case]()
    fin, collects = _finalize_with(monkeypatch, mod)
    assert len(collects) == 1
    assert set(fin) == {"gc_s", "empty_cache_s"} and fin["gc_s"] >= 0.0


def test_a_swipe_with_the_gc_policy_installed_requests_once_and_never_collects(monkeypatch):
    """The whole swipe (two candidates, run_swipe's release_s sums finalize's dict): no full collection at
    all, one request at its end."""
    pytest.importorskip("kornia")
    import sys
    import core.swipe_runner as sr
    from core.swipe_runner import CandidateRunner
    collects = []
    monkeypatch.setattr(sr, "gc", SimpleNamespace(collect=lambda *a: collects.append(a) or 0))
    mod, asked = _gc_policy_module(installed=True)
    monkeypatch.setitem(sys.modules, "core.gc_policy", mod)
    runner = CandidateRunner(SwipeInput(half=False), device="cpu", loader=lambda p: _BrightBoxModel())
    frames = [_facade_frame()] * 2
    obs = [Observation(groups=[(460.0, 750.0, 5000.0)])] * 2
    out = run_swipe(frames, obs, CandidatePlan([X1280, L1920]), X1280, runner, lambda c: c.engine_file,
                    SwipeParams(min_groups=1))
    assert collects == [] and asked == ["swipe ended"]
    assert out.result is not None and out.timing["release_s"] >= 0.0


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
    try:                                   # 8.4.1xx captures graphs; 8.4.63 (the laptop) has no such module
        import importlib
        trt_backend = importlib.import_module("ultralytics.nn.backends.tensorrt")
    except ImportError:
        trt_backend = None
    if trt_backend is not None:
        assert getattr(trt_backend.TensorRTBackend.load_model, "_wd_wrapped", False)
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
          plate=(1, "2026-10-08T21:36:18"), plate_ok=True, frame=(1260, 1824),
          enhance=(False, False, True, 2.5, 1.2), exclusion=((10, 16), ())):
    from runtime.swipe_controller import LiveSetting
    return LiveSetting(project, cand, model_id, trt, roi=roi, plate=plate, plate_ok=plate_ok, frame=frame,
                       enhance=enhance, exclusion=exclusion)


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
    # the automatic swipe off (the default): P5's row, P5's Apply label
    assert row.detail.endswith("- in STANDBY press 'Apply l @ 1920 x 1056 (STANDBY)', under Try other settings")
    # P6, the automatic swipe on: the reload Apply's label says what it costs
    row = c.readiness(plate_ready=True, live_now=live["v"], swipe_auto="suggest")
    assert row.detail.endswith("- in STANDBY press 'Apply l @ 1920 x 1056 (STANDBY: a few seconds without "
                               "tracking)', under Try other settings")
    why, winner, conf = c.applicable(live["v"])
    assert why is None and winner.model == "yolo11l-pose"
    assert conf == getattr(c.outcome.result, "recommended_conf", None)
    assert c.status_dict()["switch"]


def test_the_controller_waits_for_frames_with_dancers_and_can_be_cancelled():
    proc = _FakeProc([], 0, ready=False)
    live = {"v": _live()}
    c, ev = _controller(proc, live, _FakeRunner())
    c.start()
    time.sleep(0.05)
    assert c.busy and c.state == "collecting" and c.text == "collecting 0 / 150 frames with a dancer on the wall"
    assert c.start() == "a swipe is already running"
    c.cancel()
    c.join(5)
    assert c.state == "cancelled" and c.outcome is None
    assert ev["result"][-1][1].startswith("swipe cancelled")
    assert c.readiness(plate_ready=True, live_now=live["v"]) is None      # nothing to say: no row


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
    for change, words in ((dict(plate=("another plate", "2026-11-26T21:00")), "the empty-wall plate changed"),
                          (dict(plate_ok=False), "the empty-wall plate is no longer valid - Calibrate"),
                          (dict(frame=(1920, 1080)), "the camera frame size changed"),
                          (dict(enhance=(True, False, True, 3.0, 1.2)), "the enhancement changed"),
                          (dict(exclusion=((16, 10), ((3, 4),))), "the excluded cells changed")):
        now = _live(**change)
        assert c.readiness(plate_ready=True, live_now=now) is None           # stale: no row
        assert c.applicable(now)[0].startswith(words)


def _latching_processor():
    """A real FrameProcessor whose foreground carries F2's latch (``_LatchingDetector``), and the swipe's live
    setting as the app builds it from it (``plate`` = plate_identity, ``plate_ok`` = plate_usable)."""
    fp, _clock = _proc()
    det = _LatchingDetector()
    fp._fg_detector = det
    return det, lambda: _live(plate=fp.plate_identity, plate_ok=fp.plate_usable)


PLATE_UNUSABLE_TEXT = ("the empty-wall plate is no longer valid - Calibrate on the empty wall, then run "
                       "'Try other settings' again")


def test_an_advice_found_before_the_plate_went_stale_is_withdrawn_with_what_to_do():
    """F2: a verdict found before the latch measured a plate that no longer fits: no readiness row, and Apply
    says what to do -- the swipe itself is refused until the empty wall is taken again."""
    det, live_now = _latching_processor()
    live = {"v": live_now()}
    c, ev = _controller(_FakeProc(_frames(), 30), live, _FakeRunner())
    c.start()
    c.join(5)
    assert c.state == "done" and c.readiness(plate_ready=True, live_now=live["v"]).status == "warn"
    assert c.applicable(live["v"])[0] is None
    det.stale_latched = True
    live["v"] = live_now()
    assert not live["v"].plate_ok
    assert c.readiness(plate_ready=False, live_now=live["v"]) is None
    assert c.applicable(live["v"]) == (PLATE_UNUSABLE_TEXT, None, None)
    assert PLATE_UNUSABLE_TEXT.isascii()


def test_a_swipe_running_when_the_plate_goes_stale_ends_without_an_advice():
    """F2 mid-swipe, while it collects and while it runs: plate_ok changes, the swipe is dropped with the
    sentence that says what to do, and no advice is left behind."""
    det, live_now = _latching_processor()
    live = {"v": live_now()}

    def latch(cand):
        if cand == L1920:
            det.stale_latched = True
            live["v"] = live_now()
    c, ev = _controller(_FakeProc(_frames(), 30), live, _FakeRunner(on_run=latch))
    c.start()
    c.join(5)
    assert c.state == "dropped" and c.outcome is None and c.text == PLATE_UNUSABLE_TEXT
    assert ev["result"][-1] == ({}, "swipe dropped: " + PLATE_UNUSABLE_TEXT)
    assert c.readiness(plate_ready=False, live_now=live["v"]) is None
    assert c.applicable(live["v"])[0].startswith("no swipe result")
    det, live_now = _latching_processor()                          # the latch while the pool fills
    live = {"v": live_now()}
    c, ev = _controller(_FakeProc([], 0, ready=False), live, _FakeRunner())
    c.start()
    time.sleep(0.05)
    assert c.state == "collecting"
    det.stale_latched = True
    live["v"] = live_now()
    c.join(5)
    assert c.state == "dropped" and c.outcome is None and c.text == PLATE_UNUSABLE_TEXT
    assert c.readiness(plate_ready=False, live_now=live["v"]) is None


def test_a_failed_live_re_run_ends_the_swipe_in_error():
    class _LiveFails(_FakeRunner):
        def run(self, images, cand, engine_path, **kw):
            return CandidateRun(cand, error="inference failed: AcceleratorError") if cand == X1280 \
                else super().run(images, cand, engine_path, **kw)
    proc = _FakeProc(_frames(), 30)
    c, ev = _controller(proc, {"v": _live()}, _LiveFails(), rerun_live=True)    # the check path re-runs it
    c.start()
    c.join(5)
    assert c.state == "error" and c.outcome is None
    assert c.text == "the live setting could not be re-run: inference failed: AcceleratorError"
    assert c.applicable(_live())[0].startswith("no swipe result")


def test_the_live_row_comes_from_the_stored_boxes_and_its_engine_never_runs():
    """The operator's swipe scores the live setting from the boxes the live pass stored with each kept frame:
    two engines run, not three; the live row says so (stored, no load, no ms of its own: the cost bar is
    ``Candidate.cost``'s for every row)."""
    class _NoLive(_FakeRunner):
        def run(self, images, cand, engine_path, **kw):
            assert cand != X1280, "the live engine must not run"
            return super().run(images, cand, engine_path, **kw)
    runner = _NoLive()
    proc = _FakeProc(_frames(), 30)
    c, ev = _controller(proc, {"v": _live()}, runner)
    c.start()
    c.join(5)
    assert c.state == "done" and X1280 not in runner.calls and len(runner.calls) == 2
    row = next(r for r in c.outcome.to_dict()["rows"] if r["label"] == "x @ 1280")
    assert row["load_s"] == 0.0 and row["frames_run"] == 30 and row["ms_p50"] is None
    assert [t for s, t, p in ev["progress"] if s == "running"] == ["trying x @ 1920 x 1056 (1/2)",
                                                                   "trying l @ 1920 x 1056 (2/2)"]


def test_stored_and_re_run_live_rows_give_the_same_verdict_on_the_same_boxes():
    """Same boxes, same scores: the stored row and a re-run that finds exactly those boxes give one verdict,
    and the excluded cells are applied once (a stored row already had them)."""
    from core.swipe import SwipeFrame
    from core.swipe_runner import live_run_from_boxes, score_runs
    frames = [SwipeFrame(float(i), np.zeros((8, 8), np.uint8), Observation(groups=[DANCER]), i % 3 == 0,
                         () if i % 3 == 0 else (ON_HER,)) for i in range(150)]
    obs = [f.obs for f in frames]
    other = CandidateRun(L1920, boxes=[[ON_HER] for _ in frames], ms=[20.0] * 150)
    rerun = CandidateRun(X1280, boxes=[list(f.boxes) for f in frames], ms=[18.0] * 150)
    a = score_runs(obs, [live_run_from_boxes(X1280, [f.boxes for f in frames]), other], X1280, SwipeParams())
    b = score_runs(obs, [rerun, other], X1280, SwipeParams())
    assert (a.winner, a.switch, a.reason) == (b.winner, b.switch, b.reason)

    class _DropAll:
        def filter(self, boxes):
            return []
    c = score_runs(obs, [live_run_from_boxes(X1280, [f.boxes for f in frames]), other], X1280, SwipeParams(),
                   exclusion=_DropAll())
    live_row = next(r for r in c.rows if r.candidate == X1280)
    assert live_row.coverage > 0                                          # the stored row is not filtered again


def test_a_confidence_change_starts_the_pool_over():
    """The stored live boxes are cut at the show's confidence: frames kept under another one start over."""
    proc, clock = _proc()
    frame = np.dstack([np.full((40, 30), 9, np.uint8)] * 3)
    for i in range(3):
        clock["t"] = float(i)
        _tap(proc, frame, _fg((15, 20, 900)))
    assert len(proc.swipe_snapshot()[0]) == 3
    proc.settings.confidence = 0.2                                        # the dial moved
    assert proc.swipe_snapshot()[0] == []                                 # not this confidence's frames
    clock["t"] = 3.0
    _tap(proc, frame, _fg((15, 20, 900)))
    frames = proc.swipe_snapshot()[0]
    assert len(frames) == 1 and frames[0].t == 3.0


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


def test_the_operators_swipe_refuses_a_ready_plate_that_is_not_usable():
    """``plate_status`` "ready" while ``plate_usable`` is False (found stale, refused for its camera, invalidated):
    'Try other settings' does not start -- its verdict would be judged against a plate that does not hold, and
    its Apply allowed -- and the operator reads the sentence he reads without a plate; usable, it starts as
    before.  The start and the readiness row ask the one predicate, ``WallDanceApp._swipe_plate_ready``."""
    pytest.importorskip("dearpygui")
    import inspect
    import app as appmod
    from runtime import api
    proc = _FakeProc(_frames(), 30)
    proc.plate_status, proc.plate_usable = "ready", False
    events = []
    fake = SimpleNamespace(processor=proc, bus=SimpleNamespace(publish=events.append), system_state=None)
    fake._swipe_plate_ready = lambda: appmod.WallDanceApp._swipe_plate_ready(fake)
    fake.swipe, ev = _controller(proc, {"v": _live()}, _FakeRunner())
    fake.swipe.plate_ready = fake._swipe_plate_ready             # what WallDanceApp.__init__ passes
    appmod.WallDanceApp._cb_run_settings_swipe(fake)
    why = "no valid empty-wall plate: Calibrate on the empty wall first"
    assert not fake.swipe.busy and fake.swipe.state == "error" and fake.swipe.text == why
    assert ev["result"][-1] == ({}, why)
    toasts = [e.message for e in events if isinstance(e, api.Toast)]
    assert toasts == [f"Swipe: {why}"]
    proc.plate_usable = True
    appmod.WallDanceApp._cb_run_settings_swipe(fake)
    fake.swipe.join(5)
    assert fake.swipe.state == "done" and fake.swipe.outcome is not None
    assert events[-1].message.startswith("Swipe: keeping 150 frames")
    proc.plate_status = "stale"                                  # not ready: refused as before
    assert appmod.WallDanceApp._swipe_plate_ready(fake) is False
    # a processor without ``plate_usable`` (a fake, an older build): ready is enough, as before
    assert appmod.WallDanceApp._swipe_plate_ready(SimpleNamespace(processor=SimpleNamespace(plate_status="ready")))
    # the start and the readiness row both call it
    assert "plate_ready=self._swipe_plate_ready," in inspect.getsource(appmod.WallDanceApp.__init__)
    assert ("plate_ready=self._swipe_plate_ready()"
            in inspect.getsource(appmod.WallDanceApp._swipe_readiness_row))
    assert "self._swipe_readiness_row()" in inspect.getsource(appmod.WallDanceApp._build_readiness_report)


def _dancing(n, lost=5):
    from core.swipe import SwipeFrame
    return [SwipeFrame(float(i), np.zeros((1824, 1260), np.uint8), Observation(groups=[DANCER]), i < lost)
            for i in range(n)]


def test_the_collection_waits_190_s_and_needs_four_fifths_of_the_frames():
    from runtime.swipe_controller import SwipeController, dancing_needed, min_frames
    c = SwipeController(processor=lambda: None, live=lambda: None, engine_path=str, plate_ready=lambda: True)
    assert c.collect_timeout_s == 190.0
    assert min_frames(SwipeParams()) == 120 and dancing_needed(SwipeParams()) == "about two and a half minutes"


def test_at_the_timeout_four_fifths_of_the_frames_still_give_a_verdict():
    proc = _FakeProc(_dancing(120), 5, ready=False)
    c, ev = _controller(proc, {"v": _live()}, _FakeRunner(), collect_timeout_s=0.05)
    c.start()
    c.join(5)
    assert c.state == "done" and c.text.endswith("(the verdict stands on 120 / 150 frames)")
    assert c.outcome.frames == 120


def test_at_the_timeout_fewer_frames_end_without_a_verdict_and_without_a_fault():
    """Under 80 % of the frames the gate does not hold (a worse setting named on mur2 8 at 90): no verdict, one
    plain sentence -- not a fault (no readiness row), nothing run."""
    runner = _FakeRunner()
    proc = _FakeProc(_dancing(119), 5, ready=False)
    live = {"v": _live()}
    c, ev = _controller(proc, live, runner, collect_timeout_s=0.05)
    c.start()
    c.join(5)
    text = ("only 119 / 150 frames with a dancer on the wall in 0 s - the swipe needs about two and a half "
            "minutes of dancing; press again later")
    assert c.state == "no_verdict" and c.text == text and c.outcome is None and runner.calls == []
    assert ev["result"][-1] == ({}, text)
    assert c.fault is None and c.readiness(plate_ready=True, live_now=live["v"]) is None


def test_at_the_timeout_no_frame_at_all_says_where_to_look():
    proc = _FakeProc([], 0, ready=False)
    c, ev = _controller(proc, {"v": _live()}, _FakeRunner(), collect_timeout_s=0.05)
    c.start()
    c.join(5)
    assert c.state == "error" and c.text == ("no frame with a dancer on the wall in 0 s (RUN on, the empty-wall "
                                             "plate valid?)")


def test_a_setting_cut_short_never_stands_in_a_verdict():
    """A run that stopped part-way without being cancelled counts as failed: left out (the live one: no verdict)."""
    class _Short(_FakeRunner):
        def __init__(self, short):
            super().__init__()
            self.short = short

        def run(self, images, cand, engine_path, **kw):
            out = super().run(images, cand, engine_path, **kw)
            if cand.model == self.short.model and cand.shape == self.short.shape:
                out.boxes, out.ms = out.boxes[:-5], out.ms[:-5]
            return out
    frames = _frames()
    plan = CandidatePlan([X1280, L1920, L1760])
    out = run_swipe([f.image for f in frames], [f.obs for f in frames], plan, X1280, _Short(L1920),
                    lambda c: c.engine_file, SwipeParams())
    run = next(r for r in out.runs if r.candidate == L1920)
    assert run.error == "ran 25 / 30 frames" and out.result is not None and out.result.winner != L1920
    out = run_swipe([f.image for f in frames], [f.obs for f in frames], plan, X1280, _Short(X1280),
                    lambda c: c.engine_file, SwipeParams())
    assert out.result is None and out.live_error == "ran 25 / 30 frames"


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


def test_readiness_row_only_for_advice_or_a_fault():
    """Advice, not a readiness condition: no row when there is nothing to say (READY reachable without a swipe,
    never claiming one ran); warn only for an operator's verdict recommending a switch, worded for STANDBY;
    fail only for a failed Apply."""
    assert check_swipe() is None
    keep = _result(X1280, 0.85, L1920, 0.86)
    assert check_swipe(result=keep) is None                             # « keep »: nothing to say
    none = _result(X1280, 0.5, None, 0)
    none.winner, none.reason = None, "no verdict: 12 dancer groups seen, 20 needed"
    assert check_swipe(result=none) is None
    switch = _result(X1280, 0.51, L1920, 0.91)
    r = check_swipe(result=switch)
    assert r.status == "warn"
    # J2 (review of 0e7fbe5): the row names the Apply button by its label; « Try other settings » would start a
    # new swipe and drop the advice.  The automatic swipe off (the default): P5's row, word for word -- also
    # when the winner's engine is built (Apply is P5's reload then)
    p5 = ("l @ 1920 x 1056 covers 91 % of the dancers, x @ 1280 51 % - in STANDBY press "
          "'Apply l @ 1920 x 1056 (STANDBY)', under Try other settings")
    assert r.detail == p5
    assert check_swipe(result=switch, seamless=True).detail == p5
    assert check_swipe(result=switch, seamless=True, swipe_auto="off").detail == p5
    # P6, the automatic swipe on: the reload Apply's label says what it costs ...
    for mode in ("suggest", "auto"):
        r = check_swipe(result=switch, swipe_auto=mode)
        assert r.status == "warn" and r.detail == (
            "l @ 1920 x 1056 covers 91 % of the dancers, x @ 1280 51 % - in STANDBY press "
            "'Apply l @ 1920 x 1056 (STANDBY: a few seconds without tracking)', under Try other settings")
        # ... and the winner's engine built: Apply is the seamless switch, usable during the show
        r = check_swipe(result=switch, seamless=True, swipe_auto=mode)
        assert r.status == "warn" and r.detail == (
            "l @ 1920 x 1056 covers 91 % of the dancers, x @ 1280 51 % - press 'Apply l @ 1920 x 1056' (under Try "
            "other settings): it switches between two frames, the show runs on")
    r = check_swipe(fault="Apply of l @ 1920 x 1056 failed: its engine did not load on TensorRT; x @ 1280 runs "
                          "again, as before - press Dismiss (rebuild yolo11l-pose_1920x1056.engine "
                          "(extra/build_engines) before applying it again)", result=switch)
    assert r.status == "fail" and "x @ 1280 runs again" in r.detail and r.detail.isascii()
    # ... a notice (warn), not a show that is not ready, once the caller says the previous setting runs again
    assert check_swipe(fault=r.detail, fault_recovered=True, result=switch).status == "warn"


def test_the_advice_goes_when_applied_dismissed_stale_or_too_old():
    proc = _FakeProc(_frames(), 30)
    live = {"v": _live()}
    c, ev = _controller(proc, live, _FakeRunner(), verdict_max_age_s=1800.0)
    c.start()
    c.join(5)
    assert c.readiness(plate_ready=True, live_now=live["v"]).status == "warn"
    c.finished_at -= 1801.0                                            # an afternoon verdict, at night
    assert c.readiness(plate_ready=True, live_now=live["v"]) is None
    assert c.applicable(live["v"])[0] == "the swipe is 30 min old: run it again"
    c.finished_at += 1801.0
    c.dismiss()
    assert c.readiness(plate_ready=True, live_now=live["v"]) is None
    assert c.applicable(live["v"])[0].startswith("dismissed")
    c.dismissed = False
    c.mark_applied(c.outcome.result.winner)
    assert c.readiness(plate_ready=True, live_now=_live(cand=c.outcome.result.winner, model_id=7)) is None
    c.set_fault("Apply of x failed")
    assert c.readiness(plate_ready=True, live_now=live["v"]).status == "fail"
    c.dismiss()
    assert c.fault is None


def test_the_verdict_stays_applicable_from_run_to_standby():
    """RUN -> STANDBY changes nothing the verdict was measured on (Apply is STANDBY only)."""
    proc = _FakeProc(_frames(), 30)
    live = {"v": _live()}
    c, ev = _controller(proc, live, _FakeRunner())
    c.start()
    c.join(5)
    proc.frames, proc.lost, proc.ready = [], 0, False                  # STANDBY: no frame processed, kept or not
    assert c.applicable(_live())[0] is None


def test_the_run_phase_has_a_ceiling():
    """Past ``run_max_s`` the swipe stops with « no verdict » rather than running on beside the show."""
    import time as _t

    class _Slow(_FakeRunner):
        def run(self, images, cand, engine_path, *, cancelled=None, **kw):
            _t.sleep(0.05)
            out = super().run(images, cand, engine_path, cancelled=cancelled, **kw)
            return out
    proc = _FakeProc(_frames(), 30)
    c, ev = _controller(proc, {"v": _live()}, _Slow(), run_max_s=0.06)
    c.start()
    c.join(5)
    assert c.state == "no_verdict" and c.text.startswith("no verdict: the swipe ran over 0 s") and c.outcome is None
    assert c.fault is None and c.readiness(plate_ready=True, live_now=_live()) is None        # not a fault


def test_the_referee_judges_at_the_shows_confidence():
    """The swipe's params take the project's live confidence (0.15 without one); the tap's « lost » too."""
    from runtime.swipe_controller import SwipeController
    proc = _FakeProc(_frames(), 30)
    proc.settings.confidence = 0.30
    c, ev = _controller(proc, {"v": _live()}, _FakeRunner())
    assert c._params(proc).conf == 0.30
    proc.settings.confidence = 0.0
    assert c._params(proc).conf == 0.15
    tap, clock = _proc()
    tap.settings.confidence = 0.4
    frame = np.zeros((40, 30, 3), np.uint8)
    _tap(tap, frame, _fg((15, 20, 900)), boxes=[(5, 5, 25, 35, 0.35)])   # a box under the show's 0.4
    frames, lost, _p, _r = tap.swipe_snapshot()
    assert tap.swipe_collector.params.conf == 0.4 and lost == 1


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


def _apply_fixture(trt=True, mode="off"):
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
    from runtime.api import SystemState
    fake = SimpleNamespace(
        swipe=c, _swipe_live_setting=lambda: live["v"], _swipe_pending_apply=None,
        system_state=SystemState.STANDBY, _swipe_shown="operator",
        # P6: no seamless switch here (the winner's engine is not built): Apply is the reload -- P5's with the
        # automatic swipe off, P6's reload path with it on
        swipe_bg=SimpleNamespace(suggestion=None, dismiss=lambda: None, mode=mode),
        swipe_switch=SimpleNamespace(can_switch=lambda w: f"its engine is not built ({w.engine_file})",
                                     dismiss=lambda: None),
        configs=SimpleNamespace(_current_project="jeudi0809_mur2"), settings=SimpleNamespace(imgsz=1280),
        models=SimpleNamespace(request_setting=request, current_model_name="yolo11x-pose",
                               model_manager=SimpleNamespace(is_using_tensorrt=lambda: trt),
                               ui=SimpleNamespace(sync_model_combo=lambda n: calls.append(("combo", n)))),
        _sync=lambda kind, name, value: calls.append((kind, name, value)),
        _swipe_set_confidence=lambda v: calls.append(("conf", v)),
        bus=SimpleNamespace(publish=lambda e: calls.append(e)))
    fake._swipe_apply_done = lambda ok: appmod.WallDanceApp._swipe_apply_done(fake, ok)
    fake._swipe_restore_done = lambda ok, w, p: appmod.WallDanceApp._swipe_restore_done(fake, ok, w, p)
    fake._apply_settings_swipe_p5 = lambda: appmod.WallDanceApp._apply_settings_swipe_p5(fake)
    appmod.WallDanceApp._cb_apply_settings_swipe(fake)
    return appmod, c, live, calls, pending, fake


@pytest.mark.parametrize("mode", ["off", "suggest"])
def test_apply_switches_then_applies_the_confidence_once_the_winner_runs_on_tensorrt(mode):
    from runtime import api
    appmod, c, live, calls, pending, fake = _apply_fixture(mode=mode)
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
    assert c.readiness(plate_ready=True, live_now=live["v"]) is None      # applied: nothing left to say


@pytest.mark.parametrize("mode", ["off", "suggest"])
def test_a_failed_load_is_a_fault_with_what_runs_and_what_to_do(mode):
    from runtime import api
    appmod, c, live, calls, pending, fake = _apply_fixture(trt=True, mode=mode)
    pending["done"](False)                                         # the model did not load: the old one stays
    assert not any(isinstance(e, tuple) and e[0] == "conf" for e in calls) and c.applied is None
    last = [e for e in calls if isinstance(e, api.SettingsSwipeProgress)][-1]
    assert last.state == "apply_failed" and "confidence is unchanged" in last.text
    row = c.readiness(plate_ready=True, live_now=live["v"])
    # the old setting never stopped: a notice to acknowledge (warn), readiness stays READY
    assert row.status == "warn" and row.detail == (
        "Apply of l @ 1920 x 1056 failed: its engine did not load; x @ 1280 still runs, as before - press Dismiss "
        "(rebuild yolo11l-pose_1920x1056.engine (extra/build_engines) before applying it again)")
    fake._cb_dismiss_swipe = lambda: appmod.WallDanceApp._cb_dismiss_swipe(fake)
    fake._cb_dismiss_swipe()
    assert c.readiness(plate_ready=True, live_now=live["v"]) is None       # Dismiss clears it


@pytest.mark.parametrize("mode", ["off", "suggest"])
def test_a_pytorch_fallback_goes_back_to_the_previous_setting(mode):
    """The engine did not load and PyTorch would run the winner far over the frame budget (review N2): the
    previous model and input come back through the same switch, and the row says so."""
    from runtime import api
    appmod, c, live, calls, pending, fake = _apply_fixture(trt=False, mode=mode)
    winner = c.outcome.result.winner
    live["v"] = _live(cand=winner, model_id=3)                     # the winner, on PyTorch
    pending["done"](True)
    assert ("switch", "yolo11x-pose", 1280, None) in calls          # back to x @ 1280, queued
    back = c.readiness(plate_ready=True, live_now=live["v"])
    assert back.status == "fail" and back.detail == (               # nothing sure runs yet: a fault
        f"Apply of {winner.label} failed: its engine did not load on TensorRT; going back to x @ 1280 (a few "
        f"seconds without tracking)")
    fake.models.model_manager.is_using_tensorrt = lambda: True
    live["v"] = _live(model_id=4)                                  # x @ 1280 runs again
    pending["done"](True)
    row = c.readiness(plate_ready=True, live_now=live["v"])
    # M1 (review of 0e7fbe5): after a rollback that worked, the row says what is true and what to press, and it
    # is a notice (warn): the show is ready again
    assert row.status == "warn" and row.detail == (
        f"Apply of {winner.label} failed: its engine did not load on TensorRT; x @ 1280 runs again, as before - "
        f"press Dismiss (rebuild {winner.engine_file} (extra/build_engines) before applying it again)")


@pytest.mark.parametrize("mode", ["off", "suggest"])
def test_a_good_apply_clears_a_failed_one(mode):
    """The fault row of a failed Apply goes on Dismiss (above) or with the next Apply that works."""
    appmod, c, live, calls, pending, fake = _apply_fixture(trt=True, mode=mode)
    pending["done"](False)
    assert c.readiness(plate_ready=True, live_now=live["v"]).status == "warn"
    c.mark_applied(c.outcome.result.winner)                        # what _swipe_apply_done does on success
    assert c.readiness(plate_ready=True, live_now=live["v"]) is None


def _run_apply_fake(seamless_why, mode="suggest"):
    import app as appmod
    from runtime.api import SystemState
    events, asked = [], []
    winner = L1920
    fake = SimpleNamespace(
        system_state=SystemState.RUN, bus=SimpleNamespace(publish=events.append), _swipe_shown="operator",
        _swipe_live_setting=lambda: _live(),
        swipe=SimpleNamespace(applicable=lambda live: (None, winner, None)),
        swipe_switch=SimpleNamespace(can_switch=lambda w: seamless_why),
        # P6's Apply: the automatic swipe on (off, Apply is P5's: test_swipe_keyoff.py)
        swipe_bg=SimpleNamespace(suggestion=None, mode=mode,
                                 request_switch=lambda w, text, on_done=None: asked.append((w, text)) or None),
        models=SimpleNamespace(request_setting=lambda *a, **k: pytest.fail("no reload during a show")))
    appmod.WallDanceApp._cb_apply_settings_swipe(fake)
    return events, asked, winner


def test_apply_is_refused_during_a_show_when_it_reloads():
    """An Apply that reloads the model (its engine must be built; camera closed, no tracking for seconds):
    STANDBY only, like the dry-run."""
    pytest.importorskip("dearpygui")
    from runtime import api
    events, asked, _ = _run_apply_fake("its engine is not built on this machine (yolo11l-pose_1920x1056.engine)")
    (toast,) = events
    assert isinstance(toast, api.Toast) and not asked
    assert toast.message == ("Apply: stop the show first - this Apply reloads the model (its engine is not built "
                             "on this machine (yolo11l-pose_1920x1056.engine)), a few seconds without tracking")


def test_a_seamless_apply_runs_during_a_show():
    """P6: the winner's engine is built: Apply loads it beside the live one and switches between two frames --
    usable in RUN, no reload."""
    pytest.importorskip("dearpygui")
    from runtime import api
    events, asked, winner = _run_apply_fake(None)
    assert asked == [(winner, "switching to l @ 1920 x 1056 (Apply)")]
    (prog,) = events
    assert isinstance(prog, api.SettingsSwipeProgress) and prog.state == "applying"
    assert prog.text == "switching to l @ 1920 x 1056 between two frames..."


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
    # a remote Apply is STANDBY only whatever the automatic swipe's key (P5's class); the key itself is control
    assert POLICY["ApplySettingsSwipe"] == "heavy" and POLICY["LoadModel"] == "heavy"
    assert POLICY["SetSwipeAuto"] == "control"
    assert POLICY["RunSettingsSwipe"] == "control" and POLICY["CancelSettingsSwipe"] == "safe"
    assert POLICY["DismissSwipeNotice"] == "safe"


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
                           swipe_bg=SimpleNamespace(stop=lambda t: calls.append(("bg-stop", t))),
                           swipe_switch=SimpleNamespace(stop=lambda t: calls.append(("switch-stop", t))),
                           tracker=SimpleNamespace(logger=SimpleNamespace(close=lambda: calls.append("log"))),
                           recorder=SimpleNamespace(close=lambda: None), running=True)
    appmod.WallDanceApp._cb_quit(fake)
    # P6: the looker -- its stop waits for a load in progress (about 3 s cold) and frees its engine -- then the
    # switch -- its stop waits for a load in progress and frees what it holds -- then the rest of the quit
    assert calls == ["cancel", ("join", 2.0), ("bg-stop", 3.0), ("switch-stop", 8.0), "log"]
    assert fake.running is False


def test_the_live_setting_is_none_while_a_model_loads():
    """A running swipe stops when the app starts loading a model (no CUDA work beside its graph capture)."""
    pytest.importorskip("dearpygui")
    import app as appmod
    models = SimpleNamespace(model=object(), _model_loaded=True, _model_loading=True,
                             current_model_name="yolo11l-pose",
                             model_manager=SimpleNamespace(is_using_tensorrt=lambda: True))
    settings = SimpleNamespace(imgsz=1280, yolo_shape=(1920, 1056), roi_enabled=True, roi_x=135, roi_y=3,
                               roi_w=965, roi_h=1771, fg_plate_path="plates/latest.npz", enhance_enabled=True,
                               enhance_lite=False, enhance_force=True, greyscale=False, bg_subtract_enabled=False,
                               bg_subtract_sensitivity=30, brightness_threshold=60, denoise_strength=0.0)
    plate = (140, "2026-10-08T21:36:18")
    fake = SimpleNamespace(models=models, settings=settings,
                           configs=SimpleNamespace(_current_project="jeudi0809_mur2"),
                           roi=SimpleNamespace(state=SimpleNamespace(source_size=(1260, 1824))),
                           enhancer=SimpleNamespace(clahe_clip=2.5, gamma=1.2),
                           processor=SimpleNamespace(plate_usable=True, plate_identity=plate,
                                                     get_exclusion=lambda: ((10, 16), {(0, 1), (0, 2)})))
    assert appmod.WallDanceApp._swipe_live_setting(fake) is None
    models._model_loading = False
    live = appmod.WallDanceApp._swipe_live_setting(fake)
    assert live.candidate == L1920 and live.trt and live.model_id == id(models.model)
    assert live.roi == (True, 135, 3, 965, 1771) and live.plate == plate and live.plate_ok
    assert live.frame == (1260, 1824) and live.enhance[:5] == (True, False, True, 2.5, 1.2)
    assert live.exclusion == ((10, 16), ((0, 1), (0, 2)))
    fake.enhancer.clahe_clip = 3.0                                 # Calibrate moved CLAHE: another scene
    assert appmod.WallDanceApp._swipe_live_setting(fake).key != live.key
