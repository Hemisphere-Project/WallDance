"""Enumerate host<-device sync call sites per frame by monkeypatching the torch entry points
that block (Tensor.cpu/.item/.numpy/.tolist/__bool__/__float__/__int__/.to(cpu),
torch.cuda.synchronize, Stream.synchronize). Records the innermost WallDance/ultralytics
frame for each call. Scratch only.
Run from application/: .venv/bin/python sync_sites.py [--trt] --imgsz 1280 --out sites.txt
"""
import argparse
import json
import sys
import traceback
from collections import Counter
from pathlib import Path

APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "tests"))
sys.path.insert(0, str(APP / "src"))
import replay  # noqa: E402
import cv2  # noqa: E402
import torch  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="yolo11x-pose")
ap.add_argument("--imgsz", type=int, default=1280)
ap.add_argument("--trt", action="store_true")
ap.add_argument("--frames", type=int, default=20)
ap.add_argument("--preview", action="store_true")
ap.add_argument("--out", required=True)
args = ap.parse_args()

manifest = json.loads((APP / "tests/scenarios/hangar-aerial.json").read_text())
config = replay.scenario_config(manifest)
video = replay._find_recording(manifest["project"], manifest["slot"])
proc = replay._build_processor(config, args.model, args.imgsz, use_gpu_path=True, use_trt=args.trt)
proc.tracker.reset()
proc.tracker.logger.start_session(str(Path(args.out).parent / "sync_sites_log"))
if args.preview:
    proc.set_preview_fps_cap(None)
cap = cv2.VideoCapture(str(video))
cap.set(cv2.CAP_PROP_POS_FRAMES, 1600)
frames = [cap.read()[1] for _ in range(args.frames + 10)]
for i, f in enumerate(frames[:10]):
    proc.process(f, need_preview=args.preview, frame_number=i)

ACTIVE = [False]
counts = Counter()


def site():
    st = traceback.extract_stack()[:-2]
    keep = [f for f in st if ("WallDance/application/src" in f.filename or "ultralytics" in f.filename
                              or "kornia" in f.filename)]
    if not keep:
        return "?"
    f = keep[-1]
    fn = f.filename.split("site-packages/")[-1].split("application/src/")[-1]
    # also the outermost WallDance frame for context
    wd = [g for g in keep if "application/src" in g.filename]
    ctx = f"{wd[-1].filename.split('application/src/')[-1]}:{wd[-1].lineno}" if wd else ""
    return f"{fn}:{f.lineno} ({f.name})  [WD: {ctx}]"


def patch_method(cls, name, label, cond=None):
    orig = getattr(cls, name)

    def w(self, *a, **k):
        if ACTIVE[0] and (cond is None or cond(self, *a, **k)):
            counts[(label, site())] += 1
        return orig(self, *a, **k)
    setattr(cls, name, w)


is_cuda = lambda self, *a, **k: getattr(self, "is_cuda", False)  # noqa: E731
patch_method(torch.Tensor, "cpu", "Tensor.cpu", is_cuda)
patch_method(torch.Tensor, "item", "Tensor.item", is_cuda)
patch_method(torch.Tensor, "tolist", "Tensor.tolist", is_cuda)
patch_method(torch.Tensor, "__bool__", "Tensor.__bool__", is_cuda)
patch_method(torch.Tensor, "__float__", "Tensor.__float__", is_cuda)
patch_method(torch.Tensor, "__int__", "Tensor.__int__", is_cuda)
patch_method(torch.Tensor, "__index__", "Tensor.__index__", is_cuda)
patch_method(torch.cuda.Stream, "synchronize", "Stream.synchronize")
_cs = torch.cuda.synchronize


def _sync(*a, **k):
    if ACTIVE[0]:
        counts[("torch.cuda.synchronize", site())] += 1
    return _cs(*a, **k)


torch.cuda.synchronize = _sync
import ultralytics.utils.ops as UOPS  # noqa: E402
_np = UOPS.convert_torch2numpy_batch


def _np_w(b):
    if ACTIVE[0]:
        counts[(f"convert_torch2numpy_batch {tuple(b.shape)} D2H", site())] += 1
    return _np(b)


UOPS.convert_torch2numpy_batch = _np_w
ACTIVE[0] = True
for i, f in enumerate(frames[10:]):
    proc.process(f, need_preview=args.preview, frame_number=10 + i)
ACTIVE[0] = False
out = [f"frames={args.frames} trt={args.trt} imgsz={args.imgsz} preview={args.preview}",
       "per-frame count | call | innermost site [outermost WallDance frame]"]
for (lab, s), n in counts.most_common():
    out.append(f"{n / args.frames:5.2f} | {lab} | {s}")
Path(args.out).write_text("\n".join(out))
print("\n".join(out))
