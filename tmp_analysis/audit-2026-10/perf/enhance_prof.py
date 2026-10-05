import sys, time
sys.path.insert(0, "/data/WallDance/application/tests"); sys.path.insert(0, "/data/WallDance/application/src")
import replay  # noqa
import cv2, numpy as np, torch
from torch.profiler import profile, ProfilerActivity
from core.gpu_pipeline import GpuPipeline, GpuPipelineSettings
cap = cv2.VideoCapture("/data/WallDance/projects/residence1-solo/recordings/slot_4_20260402_210914.avi"); cap.set(cv2.CAP_PROP_POS_FRAMES, 1600); ok, bgr = cap.read()
s = GpuPipelineSettings(enhance_enabled=True, clahe_clip=2.5, gamma=2.2, greyscale=True, brightness_threshold=131, yolo_imgsz=1280, roi_enabled=True, roi_x=127, roi_y=228, roi_w=1296, roi_h=1195)
gp = GpuPipeline(s)
gf = gp._crop_to_roi(gp._upload_to_gpu(bgr), gp._resolve_roi(1488, 1528))
for _ in range(5): gp._enhancer.enhance(gf, s)
torch.cuda.synchronize()
with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
    for _ in range(10): gp._enhancer.enhance(gf, s)
    torch.cuda.synchronize()
ka = prof.key_averages()
launches = sum(e.count for e in ka if e.key in ("cudaLaunchKernel", "cudaLaunchKernelExC", "cuLaunchKernel", "cuLaunchKernelEx"))
gpu_us = sum(e.self_device_time_total for e in ka if e.device_type.name == "CUDA") if hasattr(ka[0], "self_device_time_total") else None
print("kernel launches per enhance:", launches / 10)
print("GPU kernel time per enhance (ms):", (gpu_us or 0) / 10 / 1000)
ts = []
for _ in range(30):
    torch.cuda.synchronize(); t = time.perf_counter(); gp._enhancer.enhance(gf, s); torch.cuda.synchronize(); ts.append((time.perf_counter() - t) * 1000)
print("wall per enhance (ms) p50 %.2f min %.2f" % (np.median(ts), min(ts)))
# mono-aware alternative: CLAHE+gamma on the single channel only (no greyscale expand / ycbcr round trip)
from kornia.enhance import equalize_clahe
y = gf.tensor[:, :1]
def mono():
    g = equalize_clahe(y, clip_limit=2.5, grid_size=(8, 8))
    return torch.pow(g.clamp(0, 1), 1 / 2.2)
for _ in range(5): mono()
ts = []
for _ in range(30):
    torch.cuda.synchronize(); t = time.perf_counter(); mono(); torch.cuda.synchronize(); ts.append((time.perf_counter() - t) * 1000)
print("mono-only CLAHE+gamma wall (ms) p50 %.2f min %.2f" % (np.median(ts), min(ts)))
print(prof.key_averages().table(sort_by="self_cpu_time_total", row_limit=12))
