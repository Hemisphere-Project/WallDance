"""torch.profiler trace of N frames of FrameProcessor.process() to enumerate host<->device
sync points (cudaStreamSynchronize / cudaDeviceSynchronize / cudaMemcpy*) with their Python
call sites. Scratch only.
Run from application/: .venv/bin/python sync_trace.py [--trt] --imgsz 1280 --out sync_trace.txt
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "tests"))
sys.path.insert(0, str(APP / "src"))
import replay  # noqa: E402
import cv2  # noqa: E402
import torch  # noqa: E402
from torch.profiler import profile, ProfilerActivity  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="yolo11x-pose")
ap.add_argument("--imgsz", type=int, default=1280)
ap.add_argument("--trt", action="store_true")
ap.add_argument("--frames", type=int, default=10)
ap.add_argument("--preview", action="store_true")
ap.add_argument("--out", required=True)
args = ap.parse_args()

manifest = json.loads((APP / "tests/scenarios/hangar-aerial.json").read_text())
config = replay.scenario_config(manifest)
video = replay._find_recording(manifest["project"], manifest["slot"])
proc = replay._build_processor(config, args.model, args.imgsz, use_gpu_path=True, use_trt=args.trt)
proc.tracker.reset()
proc.tracker.logger.start_session(str(Path(args.out).parent / "sync_trace_log"))
if args.preview:
    proc.set_preview_fps_cap(None)
cap = cv2.VideoCapture(str(video))
cap.set(cv2.CAP_PROP_POS_FRAMES, 1600)
frames = [cap.read()[1] for _ in range(args.frames + 20)]
for i, f in enumerate(frames[:20]):  # warm (incl. brightness .item() cadence)
    proc.process(f, need_preview=args.preview, frame_number=i)
torch.cuda.synchronize()
with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA], with_stack=True,
             record_shapes=False) as prof:
    for i, f in enumerate(frames[20:]):
        proc.process(f, need_preview=args.preview, frame_number=20 + i)
    torch.cuda.synchronize()

SYNC_NAMES = ("cudaStreamSynchronize", "cudaDeviceSynchronize", "cudaMemcpyAsync", "cudaMemcpy",
              "cudaEventSynchronize", "cudaStreamWaitEvent")
calls = defaultdict(lambda: [0, 0.0])
evs = list(prof.events())
ops = [e for e in evs if e.stack and e.name not in SYNC_NAMES]
def _site(ev):
    best = None
    for o in ops:
        if o.thread == ev.thread and o.time_range.start <= ev.time_range.start and o.time_range.end >= ev.time_range.end:
            if best is None or (o.time_range.end - o.time_range.start) < (best.time_range.end - best.time_range.start):
                best = o
    if best is None:
        return "(no py stack)"
    st = [x for x in best.stack if ("WallDance" in x or "ultralytics" in x or "kornia" in x)]
    st = [x.replace("/data/WallDance/application/.venv/lib/python3.10/site-packages/", "").replace("/data/WallDance/application/src/", "") for x in st]
    return best.name + " @ " + " <- ".join(st[:3])
for ev in evs:
    if ev.name in SYNC_NAMES:
        key = (ev.name, _site(ev))
        calls[key][0] += 1
        calls[key][1] += ev.cpu_time_total / 1000.0
out = [f"frames profiled: {args.frames} trt={args.trt} imgsz={args.imgsz} preview={args.preview}",
       "count/frame | host ms/frame | call | site"]
for (name, site), (n, ms) in sorted(calls.items(), key=lambda kv: -kv[1][1]):
    out.append(f"{n / args.frames:6.2f} | {ms / args.frames:8.3f} | {name} | {site}")
out.append("")
out.append(prof.key_averages().table(sort_by="cuda_time_total", row_limit=25))
Path(args.out).write_text("\n".join(out))
print("\n".join(out[:40]))
