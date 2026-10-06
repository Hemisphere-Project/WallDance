"""PERF-3: PoseRunner's fast path returns exactly the regular call's detections.

Runs the smallest pose model (yolo11n-pose.pt, PyTorch) on a real image
letterboxed like the GPU pipeline does, and compares keypoints / boxes of the
fast path with plain ``model(...)`` calls bit for bit.  Also covers the
fall-back rules (first call, argument change, out-of-range input, models
without an ultralytics predictor).  Needs CUDA + models/yolo11n-pose.pt.
"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")
if not torch.cuda.is_available():
    pytest.skip("CUDA not available", allow_module_level=True)

MODEL = Path(__file__).resolve().parents[2] / "models" / "yolo11n-pose.pt"

from core.yolo_runner import PoseRunner  # noqa: E402


@pytest.fixture(scope="module")
def model():
    if not MODEL.exists():
        pytest.skip(f"{MODEL} missing")
    import core.gpu_pipeline  # noqa: F401  (kornia_rs stub before ultralytics)
    from ultralytics import YOLO
    return YOLO(str(MODEL))


def _letterboxed(img_bgr, imgsz=640, shift=0):
    import cv2
    import torch.nn.functional as F
    img = np.roll(img_bgr, shift, axis=1)
    t = torch.from_numpy(img).cuda().permute(2, 0, 1).unsqueeze(0).float() / 255.0
    t = t.flip(1)
    h, w = t.shape[2:]
    s = min(imgsz / w, imgsz / h)
    nw, nh = int(w * s), int(h * s)
    t = F.interpolate(t, size=(nh, nw), mode="bilinear", align_corners=False)
    px, py = (imgsz - nw) // 2, (imgsz - nh) // 2
    return F.pad(t, (px, imgsz - nw - px, py, imgsz - nh - py), value=0.5)


@pytest.fixture(scope="module")
def frames():
    import cv2
    from ultralytics.utils import ASSETS
    img = cv2.imread(str(Path(ASSETS) / "zidane.jpg"))
    if img is None:
        pytest.skip("ultralytics sample image missing")
    return [_letterboxed(img, shift=s) for s in (0, 37, 211)]


def _same(a, b):
    assert len(a) == len(b) == 1
    ra, rb = a[0], b[0]
    assert torch.equal(ra.keypoints.data, rb.keypoints.data)
    assert torch.equal(ra.boxes.data, rb.boxes.data)
    assert ra.orig_shape == rb.orig_shape


def test_fast_path_equals_regular_call(model, frames):
    kw = dict(imgsz=640, conf=0.25, iou=0.7, half=False)
    ref = [model(f, verbose=False, **kw) for f in frames]
    assert len(ref[0][0].boxes) > 0                     # a real detection test
    runner = PoseRunner(model)
    for f, r in zip(frames + frames, ref + ref):
        _same(runner(f, **kw), r)
    assert runner.regular_calls == 1 and runner.fast_calls == 2 * len(frames) - 1
    assert runner.enabled


def test_argument_change_and_bad_input_use_regular_call(model, frames):
    runner = PoseRunner(model)
    kw = dict(imgsz=640, conf=0.25, iou=0.7, half=False)
    runner(frames[0], **kw)
    runner(frames[1], **kw)
    assert runner.fast_calls == 1
    kw2 = dict(kw, conf=0.5)                             # conf change -> regular
    _same(runner(frames[1], **kw2), model(frames[1], verbose=False, **kw2))
    assert runner.regular_calls == 2
    big = frames[2] * 255.0                              # LoadTensor rescales + warns
    n = runner.regular_calls
    _same(runner(big, **kw2), model(big, verbose=False, **kw2))
    assert runner.regular_calls == n + 1
    # someone else re-ran the predictor with other args -> not reused blindly
    model(frames[0], verbose=False, imgsz=640, conf=0.9, iou=0.7, half=False)
    n = runner.regular_calls
    _same(runner(frames[0], **kw2), model(frames[0], verbose=False, **kw2))
    assert runner.regular_calls == n + 1


def test_models_without_predictor_always_use_the_call():
    calls = []

    def fake(t, **kw):
        calls.append(kw)
        return ["r"]

    runner = PoseRunner(fake)
    for _ in range(3):
        assert runner(torch.zeros(1, 3, 64, 64), imgsz=64, conf=0.3, iou=0.5,
                      half=True) == ["r"]
    assert len(calls) == 3 and runner.fast_calls == 0
    assert calls[0] == dict(imgsz=64, conf=0.3, iou=0.5, half=True, verbose=False)


def test_fast_path_failure_disables_it(frames):
    class Pred(SimpleNamespace):
        def preprocess(self, x):
            raise RuntimeError("ultralytics drift")

    pred = Pred(args=SimpleNamespace(conf=0.3, iou=0.5, visualize=False, augment=False,
                                     embed=None, save=False, save_txt=False,
                                     save_crop=False, show=False),
                source_type=SimpleNamespace(tensor=True), batch=(["x"],), done_warmup=True,
                model=object(), _lock=__import__("threading").Lock())

    class Model:
        predictor = pred
        n = 0

        def __call__(self, t, **kw):
            Model.n += 1
            return ["regular"]

    runner = PoseRunner(Model())
    kw = dict(imgsz=640, conf=0.3, iou=0.5, half=True)
    assert runner(frames[0], **kw) == ["regular"]        # first call: regular
    assert runner(frames[0], **kw) == ["regular"]        # fast raised -> regular
    assert not runner.enabled and "drift" in runner.disabled_reason
    assert runner(frames[0], **kw) == ["regular"] and Model.n == 3
