"""GPU micro-benchmarks (2026-11 audit, scratch only).  Synced wall time, min / p50 of N reps."""
import json, sys, time
from pathlib import Path
APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "src"))
import numpy as np, cv2, torch, torch.nn.functional as F
import core.gpu_pipeline as GPM
from core.gpu_pipeline import GpuEnhancer, GpuFrame, GpuPipelineSettings, GpuPipeline

dev = torch.device("cuda")
N = 60


def bench(fn, n=N, warm=8):
    for _ in range(warm):
        fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(n):
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        fn()
        torch.cuda.synchronize()
        ts.append((time.perf_counter() - t0) * 1e3)
    a = np.array(ts)
    return round(float(a.min()), 3), round(float(np.percentile(a, 50)), 3)


def bench_nosync(fn, n=N, warm=8):
    """launch-only wall (what the main thread pays before the next sync)."""
    for _ in range(warm):
        fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(n):
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1e3)
    a = np.array(ts)
    return round(float(a.min()), 3), round(float(np.percentile(a, 50)), 3)


res = {}
# a real frame: hangar-aerial slot, frame 1600
cap = cv2.VideoCapture("/data/WallDance/projects/3_TANGO_HANGAR-whitebg2/recordings/slot_4_20260402_210914.avi")
cap.set(cv2.CAP_PROP_POS_FRAMES, 1600)
ok, bgr = cap.read()
ok2, bgr2 = cap.read()
cap.release()
mono = np.ascontiguousarray(bgr[:, :, 0])
mono2 = np.ascontiguousarray(bgr2[:, :, 0])
H, W = mono.shape
res["frame"] = [W, H]

# ---------------------------------------------------------------- IDS read path
pinned = torch.empty((H, W), dtype=torch.uint8).pin_memory()
stream = torch.cuda.Stream()


def read_now():
    pinned.copy_(torch.from_numpy(mono))
    with torch.cuda.stream(stream):
        g = pinned.cuda(non_blocking=True)
    stream.synchronize()
    f = g.float().mul_(1.0 / 255.0).clamp_(0.0, 1.0)
    return f.unsqueeze(0).unsqueeze(0).expand(1, 3, -1, -1).contiguous()


def read_lean(roi=(127, 228, 1296, 1195)):
    pinned.copy_(torch.from_numpy(mono))
    with torch.cuda.stream(stream):
        g = pinned.cuda(non_blocking=True)
    stream.synchronize()
    x, y, w, h = roi
    return g[y:y + h, x:x + w].float().mul_(1.0 / 255.0)[None, None]


res["read_gpu now (pin copy+H2D+sync+float+3ch contiguous, full frame)"] = bench(read_now)
res["read_gpu lean (pin copy+H2D+sync, ROI crop, float 1ch)"] = bench(read_lean)
res["cpu GRAY2BGR full frame (cached BGR)"] = None
t = []
for _ in range(60):
    t0 = time.perf_counter(); b = cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR); t.append((time.perf_counter() - t0) * 1e3)
res["cpu GRAY2BGR full frame (cached BGR)"] = (round(min(t), 3), round(float(np.median(t)), 3))
t = []
for _ in range(60):
    t0 = time.perf_counter(); g0 = np.ascontiguousarray(b[228:228 + 1195, 127:127 + 1296, 0]); t.append((time.perf_counter() - t0) * 1e3)
res["cpu channel-0 ROI copy back to mono (mog2_cvt, mono_raw)"] = (round(min(t), 3), round(float(np.median(t)), 3))

# ---------------------------------------------------------------- enhance 3ch vs 1ch
enh = GpuEnhancer()
gs = GpuPipelineSettings()
gs.enhance_enabled = True; gs.enhance_force = True; gs.clahe_clip = 2.5; gs.gamma = 2.2
gp = GpuPipeline(gs)
full3 = read_now()
for name, (x, y, w, h) in {"roi1296x1195": (127, 228, 1296, 1195), "strip1304x566": (92, 401, 1304, 566)}.items():
    t3 = full3[:, :, y:y + h, x:x + w]
    t1 = t3[:, 0:1].contiguous()

    def e3():
        f, _ = enh.enhance(GpuFrame(t3, is_bgr=False), gs)
        return gp._prepare_yolo_input(f, 1280)[0]

    def e1():
        yv = enh._clahe(t1, clip_limit=gs.clahe_clip, grid_size=(gs.clahe_grid, gs.clahe_grid))
        yv = torch.pow(yv.clamp(0, 1), 1.0 / gs.gamma)
        s = min(1280 / w, 1280 / h)
        nw, nh = int(w * s), int(h * s)
        yv = F.interpolate(yv, size=(nh, nw), mode="bilinear", align_corners=False)
        px, py = (1280 - nw) // 2, (1280 - nh) // 2
        yv = F.pad(yv, (px, 1280 - nw - px, py, 1280 - nh - py), value=0.5)
        return yv.expand(1, 3, -1, -1)

    res[f"enhance+letterbox NOW 3ch ycbcr [{name}] synced"] = bench(e3)
    res[f"enhance+letterbox NOW 3ch ycbcr [{name}] launch-only"] = bench_nosync(e3)
    res[f"enhance+letterbox 1ch (CLAHE+gamma on mono, expand at the end) [{name}] synced"] = bench(e1)
    res[f"enhance+letterbox 1ch [{name}] launch-only"] = bench_nosync(e1)
    a = e3(); b1 = e1()
    # the ycbcr round trip of R=G=B is not bit-exact; report the difference
    res[f"max |3ch - 1ch| [{name}] (0..1 scale)"] = round(float((a - b1).abs().max()), 6)

# ---------------------------------------------------------------- GPU clean-plate foreground
x, y, w, h = 127, 228, 1296, 1195
gmono = torch.from_numpy(mono).to(dev)
gmono2 = torch.from_numpy(mono2).to(dev)
roi_u8 = gmono2[y:y + h, x:x + w]
plate = F.avg_pool2d(gmono[y:y + h, x:x + w].float()[None, None], 4)      # (1,1,h/4,w/4)
sigma = 0.6
boxes = torch.tensor([[40, 60, 70, 140], [200, 80, 230, 160]], device=dev)   # 2 slots, plate px


def fg_gpu(k_boxes=2, download_mask=False):
    cur = F.avg_pool2d(roi_u8.float()[None, None], 4)
    ok = plate >= 3.0
    ratio = (cur / plate.clamp_min(1e-3))[ok]
    gain = ratio.median() if ratio.numel() else torch.tensor(1.0, device=dev)
    gain = gain.clamp(0.25, 4.0)
    thr = torch.clamp(4.0 * sigma * gain.clamp_min(1.0).sqrt(), min=2.0)
    ad = (cur - gain * plate).abs()
    m = (ad > thr).float()
    m = F.max_pool2d(-F.max_pool2d(-m, 3, 1, 1), 3, 1, 1)                  # open 3
    m = -F.max_pool2d(-F.max_pool2d(m, 7, 1, 3), 7, 1, 3)                  # close 7
    ii = F.pad(m[0, 0].cumsum(0).cumsum(1), (1, 0, 1, 0))                    # integral image
    b = boxes[:k_boxes]
    sup = (ii[b[:, 3], b[:, 2]] - ii[b[:, 1], b[:, 2]] - ii[b[:, 3], b[:, 0]] + ii[b[:, 1], b[:, 0]])
    out = torch.cat([sup, m.mean().view(1), gain.view(1)])
    r = out.cpu()                                                          # one tiny D2H (sync)
    if download_mask:
        return r, m[0, 0].byte().cpu()                                     # ~97 kB for CPU CC
    return r


res["GPU foreground (pool4, gain median, thr, open3/close7, 2 box supports, 1 tiny D2H) ROI 1296x1195"] = bench(fg_gpu)
res["GPU foreground + mask D2H (324x298 u8) for CPU blobs"] = bench(lambda: fg_gpu(2, True))


def fg_gpu_nomedian():
    cur = F.avg_pool2d(roi_u8.float()[None, None], 4)
    gain = (cur.sum() / plate.sum()).clamp(0.25, 4.0)
    thr = torch.clamp(4.0 * sigma * gain.clamp_min(1.0).sqrt(), min=2.0)
    m = ((cur - gain * plate).abs() > thr).float()
    m = F.max_pool2d(-F.max_pool2d(-m, 3, 1, 1), 3, 1, 1)
    m = -F.max_pool2d(-F.max_pool2d(m, 7, 1, 3), 7, 1, 3)
    return torch.stack([m.mean(), gain]).cpu(), m[0, 0].byte().cpu()


res["GPU foreground, mean-ratio gain (no median) + mask D2H"] = bench(fg_gpu_nomedian)

# GPU frame-diff at 1/2 res for "moving now" + mask D2H
prev_h = F.avg_pool2d(gmono[y:y + h, x:x + w].float()[None, None], 2)


def diff_gpu():
    cur = F.avg_pool2d(roi_u8.float()[None, None], 2)
    d = ((cur - prev_h).abs() > 15).byte()
    return d[0, 0].cpu()


res["GPU frame-diff 1/2 res + mask D2H (648x597 u8)"] = bench(diff_gpu)

# CPU reference: the shipped ForegroundDetector on the same ROI
from core.foreground import CleanPlate, ForegroundDetector
cp = CleanPlate.from_frames([mono, mono2, mono, mono2, mono, mono2, mono, mono2, mono], source="bench")
fd = ForegroundDetector(cp)
roi_np = np.ascontiguousarray(mono2[y:y + h, x:x + w])
cv2.setNumThreads(4)
t = []
for _ in range(60):
    t0 = time.perf_counter(); fd.process(roi_np, x, y, (W, H)); t.append((time.perf_counter() - t0) * 1e3)
res["CPU ForegroundDetector.process (shipped) ROI 1296x1195"] = (round(min(t), 3), round(float(np.median(t)), 3))
t = []
for _ in range(60):
    t0 = time.perf_counter(); d = cv2.resize(roi_np.astype(np.float32), (w // 4, h // 4), interpolation=cv2.INTER_AREA); t.append((time.perf_counter() - t0) * 1e3)
res["  of which _down: astype(float32) full-res + INTER_AREA /4"] = (round(min(t), 3), round(float(np.median(t)), 3))
t = []
for _ in range(60):
    t0 = time.perf_counter(); d = cv2.resize(roi_np, (w // 4, h // 4), interpolation=cv2.INTER_AREA).astype(np.float32); t.append((time.perf_counter() - t0) * 1e3)
res["  variant: INTER_AREA /4 on uint8 then astype (rounds to 8 bit)"] = (round(min(t), 3), round(float(np.median(t)), 3))
strip = np.ascontiguousarray(mono2[401:401 + 566, 92:92 + 1304])
t = []
for _ in range(60):
    t0 = time.perf_counter(); fd.process(strip, 92, 401, (W, H)); t.append((time.perf_counter() - t0) * 1e3)
res["CPU ForegroundDetector.process (shipped) strip 1304x566"] = (round(min(t), 3), round(float(np.median(t)), 3))

# ---------------------------------------------------------------- NVML
try:
    import pynvml
    pynvml.nvmlInit(); hnd = pynvml.nvmlDeviceGetHandleByIndex(0)

    def nv():
        pynvml.nvmlDeviceGetUtilizationRates(hnd); pynvml.nvmlDeviceGetTemperature(hnd, pynvml.NVML_TEMPERATURE_GPU)
        pynvml.nvmlDeviceGetMemoryInfo(hnd); pynvml.nvmlDeviceGetPowerUsage(hnd)
    t = []
    for _ in range(200):
        t0 = time.perf_counter(); nv(); t.append((time.perf_counter() - t0) * 1e3)
    res["NVML 4 queries (gui.get_gpu_stats), Linux"] = (round(min(t), 3), round(float(np.median(t)), 3), round(float(np.percentile(t, 95)), 3), round(max(t), 3))
except Exception as e:  # noqa: BLE001
    res["NVML"] = f"unavailable: {e}"

[print(f"{str(v):34s} {k}") for k, v in res.items()]
