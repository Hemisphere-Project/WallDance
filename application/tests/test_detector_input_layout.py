"""X5 (rehearsal build): the tensor handed to the detector is dense NCHW, whatever produced it.

The TensorRT engine binds the input's ``data_ptr()`` and reads it as a dense (1, 3, H, W) buffer (ultralytics
8.4.63, the laptop's, on every frame; 8.4.173 when it runs without its CUDA graph -- with it, the input is first
copied into the engine's own dense buffer, which hides the bug).  With « enhance_lite » on, the GPU show path
handed it a channels-last tensor (``torch.pow`` on the upload's HWC-strided crop keeps that layout, and
``F.interpolate`` / ``F.pad`` keep it too): the engine read scrambled pixels and found no box at all, even at
confidence 0.02 (r2-1126 x1dark probe: 0.0 boxes a frame, 2.0 with the same pixels made contiguous).  A crop the
size of the network input (no resize, no pad) handed the crop VIEW over, and greyscale an expanded tensor: the
same hazard on other paths.  On mgr-ran, 60 frames of jeudi0809_mur2 take 7 at yolo11l-pose @ 1920 x 1056 with
the engine reading data_ptr(): lite 0.00 boxes a frame before the guard, 2.07 after (CLAHE off: 2.07)
(r2-1126 gates/lite/trt_check.py).

These tests drive the real ``FrameProcessor`` entry points -- ``process`` (a BGR frame, uploaded) and
``process_gpu_direct`` (the IDS camera's tensor) -- with a stand-in detector that records what it is handed,
for every enhancement (off / lite / full) x network input (the square, the 1920 x 1056 rectangle) x ROI (none,
a crop, a crop the size of the input), and compare the buffer AS THE ENGINE READS IT with a CPU reference: the
numpy crop, ``core.enhancer``'s OpenCV enhancement (LUT gamma, CLAHE on Y), an OpenCV letterbox.

CPU tensors by default (the hazard is memory layout, not CUDA).  CLAHE costs ~4 s a frame on the CPU, so the
CPU cases run the full branch at clip 1.0 (gamma on Y through the YCbCr round trip, CLAHE skipped) and one
case with CLAHE; the CUDA cases run CLAHE everywhere.  They run only when the caller holds the GPU lock:
    WD_GPU_LOCK_HELD=1 flock /tmp/wd-gpu.lock .venv/bin/python -m pytest tests/test_detector_input_layout.py
``WD_X5_REPORT=<file>`` appends one line per checked tensor (layout and error against the reference).
"""
import os

import numpy as np
import pytest

torch = pytest.importorskip("torch")
cv2 = pytest.importorskip("cv2")

import core.gpu_pipeline as gp  # noqa: E402
import core.pipeline as pl  # noqa: E402
from core.enhancer import Enhancer, EnhancerSettings, ImageEnhancer  # noqa: E402
from core.pipeline import FrameProcessor, ProcessingSettings  # noqa: E402

GAMMA = 1.4
CLIP = 2.5                           # CLAHE on (the CUDA cases, one CPU case)
CLIP_CPU = 1.0                       # CLAHE skipped: the full branch's YCbCr round trip only
SQUARE = 1280
RECT = (1920, 1056)                  # (height, width): the façade rectangle
FRAME_HW = (1952, 1312)              # big enough for a crop the size of either input
CROP = (101, 37, 1003, 1499)         # x, y, w, h: resized and padded into either input
EXACT_XY = (17, 11)                  # where the crop the size of the input starts

# Tolerances against the CPU reference, in [0, 1] units (one grey level = 1/255).
#   off:  the same bilinear letterbox in float (OpenCV against torch);
#   lite: plus core.enhancer's uint8 gamma LUT, which truncates (< 1 level);
#   full: plus OpenCV's YCrCb in uint8 and its CLAHE against kornia's (FastClahe is bit-identical to kornia):
#         two implementations a few levels apart at most (5.7 max, 1.5 mean measured on this kind of frame).
# A layout scramble is 20-70 levels off on average.
TOL_MAX = {"off": 1e-4, "lite": 1.0 / 255 + 1e-4, "full": 8.0 / 255}
TOL_MEAN = {"off": 1e-5, "lite": 1.0 / 255, "full": 2.0 / 255}

_GPU_OK = torch.cuda.is_available() and os.environ.get("WD_GPU_LOCK_HELD") == "1"
_GPU_WHY = ("CUDA not available" if not torch.cuda.is_available() else
            "CUDA cases run only under the GPU lock: WD_GPU_LOCK_HELD=1 flock /tmp/wd-gpu.lock pytest ...")
DEVICES = ["cpu", pytest.param("cuda", marks=pytest.mark.skipif(not _GPU_OK, reason=_GPU_WHY))]


# ---------------------------------------------------------------------------------------------- inputs
@pytest.fixture(scope="module")
def frames():
    """A colour BGR frame (blobs + texture + noise, dark like a night wall) and the IDS camera's mono frame."""
    rng = np.random.default_rng(7)
    h, w = FRAME_HW
    coarse = cv2.resize(rng.uniform(0, 1, (h // 8, w // 8)).astype(np.float32), (w, h),
                        interpolation=cv2.INTER_CUBIC)
    fine = cv2.GaussianBlur(rng.uniform(0, 1, (h, w)).astype(np.float32), (0, 0), 3)
    norm = lambda a: (a - a.min()) / (a.max() - a.min())  # noqa: E731
    m = norm(coarse) * 90 + norm(fine) * 60 + rng.normal(0, 3, (h, w))
    bgr = np.stack([m * 1.1, m, m * 0.8 + 10], -1).clip(0, 255).astype(np.uint8)
    mono = m.clip(0, 255).astype(np.uint8)
    return bgr, mono


def _ids_tensor(mono, device):
    """IDSCamera._mono_to_gpu_bgr: mono8 -> float [0, 1] -> expanded to 3 channels -> .contiguous()."""
    t = torch.from_numpy(mono).to(device).float().mul_(1.0 / 255.0).clamp_(0.0, 1.0)
    return t.unsqueeze(0).unsqueeze(0).expand(1, 3, -1, -1).contiguous()


def _roi(kind, target_hw):
    if kind == "none":
        return None
    if kind == "crop":
        return CROP
    th, tw = target_hw
    return (EXACT_XY[0], EXACT_XY[1], tw, th)       # exactly the network input: no resize, no pad


# ---------------------------------------------------------------------------------------------- reference
def cpu_reference(bgr, enh, roi, target_hw, clip, greyscale=False):
    """(1, 3, H, W) float32 RGB: numpy crop -> core.enhancer (OpenCV) -> OpenCV letterbox, 0.5 padding."""
    crop = bgr if roi is None else bgr[roi[1]:roi[1] + roi[3], roi[0]:roi[0] + roi[2]]
    if greyscale:
        crop = cv2.cvtColor(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    if enh == "lite":
        ie = ImageEnhancer()
        ie.gamma = GAMMA
        crop = ie.enhance_simple(crop)
    elif enh == "full":
        crop = Enhancer()._enhance_cpu(crop, EnhancerSettings(enabled=True, clahe_clip=clip, clahe_grid=8,
                                                              gamma=GAMMA))
    rgb = np.ascontiguousarray(crop[..., ::-1]).astype(np.float32) / 255.0
    h, w = rgb.shape[:2]
    th, tw = target_hw
    scale = min(tw / w, th / h)
    nw, nh = int(w * scale), int(h * scale)
    if (nw, nh) != (w, h):
        rgb = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    px, py = (tw - nw) // 2, (th - nh) // 2
    out = np.full((th, tw, 3), 0.5, np.float32)
    out[py:py + nh, px:px + nw] = rgb
    return out.transpose(2, 0, 1)[None]


def engine_view(t):
    """What a reader of ``t.data_ptr()`` taking a dense (1, 3, H, W) buffer gets (TensorRT's input binding),
    as a CPU array; None when that buffer would run past the tensor's memory."""
    n = t.numel()
    if t.untyped_storage().nbytes() // t.element_size() - t.storage_offset() < n:
        return None
    dense, s = [], 1
    for d in reversed(t.shape):
        dense.insert(0, s)
        s *= d
    return torch.as_strided(t, tuple(t.shape), tuple(dense), t.storage_offset()).detach().cpu().numpy()


# ---------------------------------------------------------------------------------------------- harness
class _Detector:
    """Stands for the YOLO engine: records the tensor it is handed, finds nobody."""

    def __init__(self):
        self.seen = []

    def __call__(self, tensor, **_kw):
        self.seen.append(tensor)
        return []


@pytest.fixture
def on_device(monkeypatch):
    """Point the GPU pipeline at a device.  On the CPU: the pinned upload buffer is the tensor itself and the
    GPU path is allowed on (kornia is pure torch); nothing else of the code changes."""
    def setup(device):
        if device == "cpu":
            monkeypatch.setattr(gp, "DEVICE", torch.device("cpu"))
            monkeypatch.setattr(torch.Tensor, "pin_memory", lambda self, *a, **k: self)
            monkeypatch.setattr(pl, "GPU_PIPELINE_AVAILABLE", True)
        return torch.device(device)
    return setup


def _processor(enh, shape, roi, clip, *, greyscale=False, force=True, denoise=0.0):
    s = ProcessingSettings(confidence=0.15, imgsz=SQUARE, use_fp16=False, enhance_enabled=enh != "off",
                           enhance_lite=enh == "lite", enhance_force=force, person_height_px=110,
                           use_gpu_path=True, yolo_shape=shape, osc_enabled=False, greyscale=greyscale,
                           denoise_strength=denoise)
    if roi is not None:
        s.roi_enabled = True
        s.roi_x, s.roi_y, s.roi_w, s.roi_h = roi
    enhancer = ImageEnhancer()
    enhancer.gamma = GAMMA
    enhancer.clahe_clip = clip
    det = _Detector()
    proc = FrameProcessor(model=det, settings=s, enhancer=enhancer)
    assert proc.gpu_path_active and proc._gpu_pipeline is not None
    if gp.KORNIA_AVAILABLE:
        proc._gpu_pipeline._enhancer._gpu_available = True      # the full branch on CPU tensors too
    return proc, det


def _run(proc, entry, frames, device):
    bgr, mono = frames
    if entry == "process":
        proc.process(bgr, need_preview=False, frame_number=0)
        return bgr
    mono_bgr = cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)
    proc.process_gpu_direct(_ids_tensor(mono, device), need_preview=False, frame_number=0, raw_frame=mono_bgr)
    return mono_bgr


def _check(det, ref, enh, target_hw, label, slack_max=0.0, slack_mean=0.0):
    assert len(det.seen) == 1, f"{label}: the detector was called {len(det.seen)} times"
    t = det.seen[0]
    th, tw = target_hw
    raw = engine_view(t)
    if raw is None:
        d, err = None, "the engine would read past the tensor's memory"
    else:
        d = np.abs(raw - ref)
        err = f"engine-read error max {d.max() * 255:.3f} / mean {d.mean() * 255:.4f} levels"
    detail = (f"{label}: shape {tuple(t.shape)} dtype {t.dtype} stride {t.stride()} "
              f"contiguous {t.is_contiguous()}; {err}")
    report = os.environ.get("WD_X5_REPORT")
    if report:
        with open(report, "a") as f:
            f.write(detail + "\n")
    assert tuple(t.shape) == (1, 3, th, tw), detail
    assert t.dtype == torch.float32, detail
    assert t.is_contiguous(), detail
    assert d is not None, detail
    assert d.max() <= TOL_MAX[enh] + slack_max and d.mean() <= TOL_MEAN[enh] + slack_mean, detail


# ---------------------------------------------------------------------------------------------- the gate
@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize("entry", ["process", "direct"])
@pytest.mark.parametrize("roi_kind", ["none", "crop", "exact"])
@pytest.mark.parametrize("shape", [None, RECT], ids=["square", "rect"])
@pytest.mark.parametrize("enh", ["off", "lite", "full"])
def test_detector_gets_dense_nchw_equal_to_the_cpu_reference(enh, shape, roi_kind, entry, device, frames,
                                                            on_device):
    if enh == "full" and not gp.KORNIA_AVAILABLE:
        pytest.skip("kornia not available: the full enhancement is a no-op on this install")
    dev = on_device(device)
    clip = CLIP if device == "cuda" else CLIP_CPU
    target = shape or (SQUARE, SQUARE)
    roi = _roi(roi_kind, target)
    proc, det = _processor(enh, shape, roi, clip)
    src = _run(proc, entry, frames, dev)
    _check(det, cpu_reference(src, enh, roi, target, clip), enh, target,
           f"{enh}/{'rect' if shape else 'square'}/{roi_kind}/{entry}/{device}/clip{clip}")


@pytest.mark.parametrize("entry", ["process", "direct"])
def test_full_with_clahe_on_cpu(entry, frames, on_device):
    """CLAHE itself once on CPU tensors (the CUDA cases run it everywhere)."""
    if not gp.KORNIA_AVAILABLE:
        pytest.skip("kornia not available: the full enhancement is a no-op on this install")
    dev = on_device("cpu")
    proc, det = _processor("full", RECT, CROP, CLIP)
    src = _run(proc, entry, frames, dev)
    _check(det, cpu_reference(src, "full", CROP, RECT, CLIP), "full", RECT, f"full/rect/crop/{entry}/cpu/clip{CLIP}")


# ---------------------------------------------------------------------------------------------- other paths
@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize("entry", ["process", "direct"])
@pytest.mark.parametrize("enh", ["off", "lite", "full"])
def test_greyscale_is_dense_too(enh, entry, device, frames, on_device):
    """Greyscale expands one channel to three (a stride-0 view); with a crop the size of the input nothing
    after it copies: never handed over as it is."""
    if enh == "full" and not gp.KORNIA_AVAILABLE:
        pytest.skip("kornia not available")
    dev = on_device(device)
    clip = CLIP if device == "cuda" else CLIP_CPU
    roi = _roi("exact", RECT)
    proc, det = _processor(enh, RECT, roi, clip, greyscale=True)
    src = _run(proc, entry, frames, dev)
    # greyscale in float (GPU) against cvtColor's rounded uint8 (CPU): up to one level more
    _check(det, cpu_reference(src, enh, roi, RECT, clip, greyscale=True), enh, RECT,
           f"grey/{enh}/rect/exact/{entry}/{device}/clip{clip}", slack_max=1.0 / 255, slack_mean=0.5 / 255)


@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize("entry", ["process", "direct"])
@pytest.mark.parametrize("roi_kind", ["crop", "exact"])
def test_bright_scene_bypass_and_denoise_are_dense(roi_kind, entry, device, frames, on_device):
    """The auto-bypass (a bright scene: lite hands the crop over untouched) and the temporal denoise with lite
    (its first frame is the frame itself): dense, equal to the reference of what they did."""
    dev = on_device(device)
    bgr, mono = frames
    bright = (np.clip(bgr.astype(np.int16) + 120, 0, 255).astype(np.uint8),
              np.clip(mono.astype(np.int16) + 120, 0, 255).astype(np.uint8))
    roi = _roi(roi_kind, RECT)
    for label, kw, ref_enh in (("bypass", dict(force=False), "off"), ("denoise", dict(denoise=0.5), "lite")):
        proc, det = _processor("lite", RECT, roi, CLIP_CPU, **kw)
        proc._gpu_pipeline._enhancer._brightness_interval = 1       # measure the brightness on this frame
        src = _run(proc, entry, bright if label == "bypass" else frames, dev)
        if label == "bypass":
            assert proc._timing.get("enhance_active") is False, "the bright frame was enhanced: no bypass"
        _check(det, cpu_reference(src, ref_enh, roi, RECT, CLIP_CPU), ref_enh, RECT,
               f"{label}/lite/rect/{roi_kind}/{entry}/{device}")
