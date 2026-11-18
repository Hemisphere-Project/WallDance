"""Rectangular vs square-letterbox YOLO input for a strip ROI (PyTorch FP16, eager):
(1) detection parity on the same content pixels, (2) GPU kernel time ratio (torch.profiler).
Scratch only; reads models/yolo11x-pose.pt and one corpus take."""
import sys, time, json
from pathlib import Path
APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "src"))
import numpy as np, cv2, torch, torch.nn.functional as F
from ultralytics import YOLO
cv2.setNumThreads(4)

dev = torch.device("cuda")
model = YOLO("/data/WallDance/models/yolo11x-pose.pt")
CONF = 0.15

# white-duo take (two dancers), a 1296 x 576 strip around the dancers
cap = cv2.VideoCapture("/data/WallDance/projects/4_TANGO_HANGAR-whitebg3/recordings/slot_2_20260403_101623.avi")
cap.set(cv2.CAP_PROP_POS_FRAMES, 100)
frames = []
for i in range(400):
    ok, f = cap.read()
    if not ok:
        break
    if i % 10 == 0:
        frames.append(np.ascontiguousarray(f[:, :, 0]))
cap.release()
H, W = frames[0].shape
x0, w = 137, 1296
# pick the strip rows holding the most temporal variance (where the dancers are)
st = np.stack([f[:, x0:x0 + w].astype(np.float32) for f in frames[::4]])
rowvar = st.std(0).mean(1)
hh = 576
cs = np.convolve(rowvar, np.ones(hh), mode="valid")
y0 = int(cs.argmax())
print(f"frame {W}x{H}; strip x={x0} y={y0} {w}x{hh}; {len(frames)} frames")


def prep(mono):
    t = torch.from_numpy(mono[y0:y0 + hh, x0:x0 + w]).to(dev).float().div_(255.0)[None, None]
    t = torch.pow(t.clamp(0, 1), 1.0 / 2.2)                       # the scenario's gamma, no CLAHE
    s = min(1280 / w, 1280 / hh)
    nw, nh = int(w * s), int(hh * s)
    t = F.interpolate(t, size=(nh, nw), mode="bilinear", align_corners=False)
    rect_h = ((nh + 31) // 32) * 32
    py_r = (rect_h - nh) // 2
    rect = F.pad(t, (0, 1280 - nw, py_r, rect_h - nh - py_r), value=0.5).expand(1, 3, -1, -1).contiguous()
    py_s = (1280 - nh) // 2
    sq = F.pad(t, (0, 1280 - nw, py_s, 1280 - nh - py_s), value=0.5).expand(1, 3, -1, -1).contiguous()
    return rect, py_r, sq, py_s


def dets(r, pad_y):
    b = r[0].boxes
    if b is None or len(b) == 0:
        return np.zeros((0, 6))
    xywh = b.xywh.float().cpu().numpy().copy()
    xywh[:, 1] -= pad_y
    k = r[0].keypoints
    kc = (k.xy.float().cpu().numpy() * (k.conf.float().cpu().numpy()[..., None] > 0.5)).sum(1) / \
        np.maximum((k.conf.float().cpu().numpy() > 0.5).sum(1, keepdims=True), 1)
    kc[:, 1] -= pad_y
    return np.concatenate([xywh, b.conf.float().cpu().numpy()[:, None], kc[:, :1] * 0], axis=1), kc


n_r = n_s = matched = 0
dpos, dconf, dkc = [], [], []
for m in frames:
    rect, py_r, sq, py_s = prep(m)
    rr = model(rect, imgsz=(rect.shape[2], rect.shape[3]), conf=CONF, half=True, verbose=False)
    rs = model(sq, imgsz=1280, conf=CONF, half=True, verbose=False)
    a = dets(rr, py_r); b = dets(rs, py_s)
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        n_r += 0 if isinstance(a, np.ndarray) else len(a[0]); n_s += 0 if isinstance(b, np.ndarray) else len(b[0])
        continue
    (A, ka), (B, kb) = a, b
    n_r += len(A); n_s += len(B)
    used = set()
    for i in range(len(A)):
        d = np.hypot(B[:, 0] - A[i, 0], B[:, 1] - A[i, 1])
        j = int(d.argmin())
        if d[j] < 0.25 * A[i, 3] and j not in used:
            used.add(j); matched += 1
            dpos.append(d[j] / A[i, 3]); dconf.append(abs(A[i, 4] - B[j, 4]))
            dkc.append(float(np.hypot(*(ka[i] - kb[j]))) / A[i, 3])
print(json.dumps({"rect_shape": list(rect.shape), "dets_rect": n_r, "dets_square": n_s, "matched": matched,
                  "box centre diff / height: p50, p95, max": [round(float(np.percentile(dpos, q)), 4) for q in (50, 95, 100)],
                  "keypoint-mean diff / height: p50, p95, max": [round(float(np.percentile(dkc, q)), 4) for q in (50, 95, 100)],
                  "|conf diff|: p50, p95, max": [round(float(np.percentile(dconf, q)), 4) for q in (50, 95, 100)]}))

# ---- GPU kernel time by input shape (eager FP16; wall time is launch-bound on this CPU)
from torch.profiler import profile, ProfilerActivity
net = model.predictor.model if model.predictor is not None else None
x_r, _, x_s, _ = prep(frames[0])
x960 = F.interpolate(x_s, size=(960, 960))
for name, x in (("1280x1280", x_s), (f"{x_r.shape[2]}x1280", x_r), ("960x960", x960),
                ("960x1280", F.interpolate(x_s, size=(960, 1280)))):
    xh = x.half()
    with torch.inference_mode():
        for _ in range(3):
            net(xh)
        torch.cuda.synchronize()
        with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
            for _ in range(8):
                net(xh)
            torch.cuda.synchronize()
    tot = 0.0
    for e in prof.key_averages():
        tot += getattr(e, "self_device_time_total", getattr(e, "self_cuda_time_total", 0.0))
    print(f"  GPU kernel time {name:10s}: {tot / 8 / 1000.0:7.2f} ms / forward  ({x.shape[2] * x.shape[3] / 1e6:.3f} MP)")
