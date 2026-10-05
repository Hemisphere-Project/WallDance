"""GPU micro-benchmarks (perf audit, scratch): enhance/letterbox/preview/IDS upload,
ultralytics hidden per-frame costs, and IR-marker stage candidates on GPU.
All timings are cuda-synchronized (CUDA events) unless stated.
Run from application/: .venv/bin/python gpu_micro.py --out gpu_micro.json
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/data/WallDance/application/tests")
sys.path.insert(0, "/data/WallDance/application/src")
import replay  # noqa: E402,F401  (LD_LIBRARY_PATH bootstrap re-exec)
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--reps", type=int, default=50)
args = ap.parse_args()
dev = torch.device("cuda")


def gbench(fn, reps=args.reps, warm=5):
    for _ in range(warm):
        fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        torch.cuda.synchronize()
        ts.append((time.perf_counter() - t0) * 1000)
    a = np.asarray(ts)
    return {"mean": round(float(a.mean()), 3), "p50": round(float(np.median(a)), 3),
            "p95": round(float(np.percentile(a, 95)), 3)}


res = {"device": torch.cuda.get_device_name(0), "load": os.getloadavg()}
VIDEO = "/data/WallDance/projects/residence1-solo/recordings/slot_4_20260402_210914.avi"
cap = cv2.VideoCapture(VIDEO)
cap.set(cv2.CAP_PROP_POS_FRAMES, 1600)
ok, bgr = cap.read()
cap.release()
x, y, w, h = 127, 228, 1296, 1195

from core.gpu_pipeline import GpuPipeline, GpuPipelineSettings, GpuFrame  # noqa: E402
s = GpuPipelineSettings(enhance_enabled=True, clahe_clip=2.5, gamma=2.2, greyscale=True,
                        brightness_threshold=131, yolo_imgsz=1280, roi_enabled=True,
                        roi_x=x, roi_y=y, roi_w=w, roi_h=h)
gp = GpuPipeline(s)
res["upload_bgr_1488x1528(pinned+H2D+float+flip)"] = gbench(lambda: gp._upload_to_gpu(bgr))
gf = gp._upload_to_gpu(bgr)
roi = gp._resolve_roi(1488, 1528)
gr = gp._crop_to_roi(gf, roi)
res["enhance_greyscale+CLAHE+gamma_roi_fp32"] = gbench(lambda: gp._enhancer.enhance(gr, s))
enh, _ = gp._enhancer.enhance(gr, s)
for sz in (960, 1280):
    res[f"letterbox_{sz}"] = gbench(lambda: gp._prepare_yolo_input(enh, sz))
res["preview_resize_to_roi(noop)+D2H_uint8"] = gbench(
    lambda: gp._resizer.resize(enh, target_size=(w, h)).to_numpy_bgr())
res["preview_resize_to_0.35+D2H_uint8"] = gbench(
    lambda: gp._resizer.resize(enh, target_size=(int(w * .35), int(h * .35))).to_numpy_bgr())
# whole front-end as process() does it (no preview)
res["frontend_process(no preview)"] = gbench(lambda: gp.process(bgr, preview_enabled=False))

# IDS GPU-direct path: mono -> pinned -> side stream H2D + stream sync -> float -> 3ch contiguous
from camera.ids_camera import IDSCamera  # noqa: E402
cam = IDSCamera.__new__(IDSCamera)
cam._upload_stream = torch.cuda.Stream()
for (W, H) in ((1528, 1528), (2560, 1600)):
    mono = np.random.randint(0, 255, (H, W), np.uint8)
    res[f"ids_mono_to_gpu_bgr_{W}x{H}"] = gbench(lambda: cam._mono_to_gpu_bgr(mono))
    # leaner alternative: uint8 1ch to GPU, crop ROI, convert only the ROI, no 3ch materialization
    pin = torch.empty((H, W), dtype=torch.uint8).pin_memory()

    def lean():
        pin.copy_(torch.from_numpy(mono))
        g = pin.to(dev, non_blocking=True)
        return g[y:y + h, x:x + w].float().div_(255.0)
    res[f"ids_lean_mono_upload_roi_{W}x{H}"] = gbench(lean)

# --- ultralytics hidden costs at imgsz ------------------------------------
from ultralytics.utils import ops as UOPS  # noqa: E402
for sz in (960, 1280):
    t = torch.rand(1, 3, sz, sz, device=dev)
    res[f"ul_convert_torch2numpy_batch_{sz}(orig img D2H)"] = gbench(lambda: UOPS.convert_torch2numpy_batch(t))
    res[f"ul_tensor_check_max_{sz}"] = gbench(lambda: bool(t.max() > 1.0))
    res[f"half_cast_{sz}"] = gbench(lambda: t.half())
kp = torch.rand(4, 17, 3, device=dev)
res["small_D2H_keypoints(4x17x3)"] = gbench(lambda: kp.cpu().numpy())
res["empty_sync"] = gbench(lambda: None)

# --- IR marker stage on GPU -------------------------------------------------
for (W, H) in ((1528, 1528), (2560, 1600)):
    g8 = torch.randint(0, 200, (H, W), dtype=torch.uint8, device=dev)
    for i in range(12):  # sprinkle a dozen 5x5 saturated markers
        yy, xx = 100 + i * 100 % (H - 200), 100 + i * 173 % (W - 200)
        g8[yy:yy + 5, xx:xx + 5] = 255
    gf_ = g8.float()

    def peaks():
        m = (gf_ >= 245).float()
        # local-max NMS on the thresholded intensity -> one seed per blob
        v = gf_ * m
        mx = F.max_pool2d(v[None, None], 7, stride=1, padding=3)[0, 0]
        seeds = torch.nonzero((v == mx) & (m > 0))
        return seeds[:256].cpu()
    res[f"gpu_marker_thresh+maxpoolNMS+nonzero+D2H_{W}x{H}"] = gbench(peaks)

    def thresh_only_d2h():
        return (g8 >= 245).cpu()
    res[f"gpu_thresh+mask_D2H_{W}x{H}"] = gbench(thresh_only_d2h)
    try:
        from kornia.contrib import connected_components
        mm = (gf_ >= 245).float()[None, None]
        res[f"kornia_connected_components_{W}x{H}(iters=100)"] = gbench(
            lambda: connected_components(mm, num_iterations=100), reps=10, warm=2)
    except Exception as e:  # noqa: BLE001
        res["kornia_cc_error"] = str(e)[:200]
    # CPU baseline on the same synthetic frame
    c8 = g8.cpu().numpy()
    for nt in (1, 8):
        cv2.setNumThreads(nt)
        t0 = time.perf_counter()
        for _ in range(30):
            _, mk = cv2.threshold(c8, 245, 255, cv2.THRESH_BINARY)
            cv2.connectedComponentsWithStats(mk, connectivity=8)
        res[f"cpu_thresh+CCstats_{W}x{H}_threads{nt}"] = round((time.perf_counter() - t0) / 30 * 1000, 3)

# torch GPU background model (running-avg, what a GPU motion model would cost)
for (W, H) in ((1296, 1195), (2560, 1600)):
    bg = torch.rand(H, W, device=dev)
    cur = torch.rand(H, W, device=dev)
    prev = torch.rand(H, W, device=dev)

    def gpu_motion():
        d = (cur - bg).abs_()
        bg.lerp_(cur, 0.001)
        fd = (cur - prev).abs_() > (6 / 255)
        fg = d > 0.05
        fgm = F.max_pool2d(-F.max_pool2d(-fg.float()[None, None], 3, 1, 1), 3, 1, 1)  # open3
        return fgm, fd
    res[f"gpu_runavg_bg+framediff+open3_{W}x{H}"] = gbench(gpu_motion)

res["load_after"] = os.getloadavg()
Path(args.out).write_text(json.dumps(res, indent=1))
print(json.dumps(res, indent=1))
