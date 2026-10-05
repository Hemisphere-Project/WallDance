"""Per-stage budget harness for WallDance's GPU show path (perf audit, scratch only).

Drives the REAL FrameProcessor (built exactly like tests/replay.py does) over a
scenario's frames and wraps every stage with timers by monkeypatching the
instance/class (no repo edits).

Modes:
  natural : no extra syncs -> the true per-frame wall cost (what the app pays);
            stage numbers here are CPU-side issue times (GPU work may be billed
            to the next sync point).
  sync    : torch.cuda.synchronize() before/after every GPU stage -> correct
            per-stage GPU attribution (but kills CPU/GPU overlap, so total is
            pessimistic).

Usage (from application/):
  .venv/bin/python <this> --model yolo11x-pose --imgsz 1280 [--trt] [--fp16]
       [--mode natural|sync] [--frames 300] [--preview] [--cv2-threads N]
       [--osc] --out result.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "tests"))
sys.path.insert(0, str(APP / "src"))
sys.path.insert(0, str(APP))

import replay  # noqa: E402  (re-execs once with LD_LIBRARY_PATH bootstrapped)
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--scenario", default=str(APP / "tests/scenarios/hangar-aerial.json"))
ap.add_argument("--model", default="yolo11x-pose")
ap.add_argument("--imgsz", type=int, default=1280)
ap.add_argument("--trt", action="store_true")
ap.add_argument("--fp16", action="store_true", help="PyTorch .pt with half=True (app default fp16:true)")
ap.add_argument("--mode", default="natural", choices=["natural", "sync"])
ap.add_argument("--frames", type=int, default=300)
ap.add_argument("--warmup", type=int, default=15)
ap.add_argument("--preview", action="store_true", help="need_preview=True with the app's 10 fps cap")
ap.add_argument("--cv2-threads", type=int, default=None, help="cv2.setNumThreads AFTER ultralytics import")
ap.add_argument("--osc", action="store_true", help="attach a real OSCSender (UDP to 127.0.0.1:59999)")
ap.add_argument("--profile-post", default=None, help="cProfile _post_yolo_chain + motion worker -> this .prof prefix")
ap.add_argument("--set", dest="sets", action="append", default=[])
ap.add_argument("--serial-motion", action="store_true", help="run the motion feed inline (no overlap) to isolate its cost")
ap.add_argument("--out", required=True)
args = ap.parse_args()

if args.cv2_threads is not None:
    cv2.setNumThreads(args.cv2_threads)

manifest = json.loads(Path(args.scenario).read_text())
config = replay.scenario_config(manifest)
replay.apply_overrides(config, args.sets)
video = replay._find_recording(manifest["project"], manifest["slot"])
start = manifest["start"]

proc = replay._build_processor(config, args.model, args.imgsz, use_gpu_path=True, use_trt=args.trt)
if not args.trt:
    proc.settings.use_fp16 = bool(args.fp16)
proc.tracker.reset()
logdir = Path(args.out).with_suffix("")
logdir = Path(str(logdir) + "_log")
logdir.mkdir(parents=True, exist_ok=True)
proc.tracker.logger.start_session(str(logdir))
if args.osc:
    from core.osc_output import OSCSender
    proc.attach_osc(OSCSender("127.0.0.1", 59999))
    proc.settings.osc_enabled = True
if args.preview:
    # app: preview_fps_cap True -> processor.set_preview_fps_cap(10.0); preview
    # size = camera * render scale (0.6834 pinned) ; ROI on -> ROI-sized download
    proc.set_preview_fps_cap(10.0)
    proc.set_preview_size(int(1488 * config.get("preview_scale", 0.35)),
                          int(1528 * config.get("preview_scale", 0.35)))

SYNC = args.mode == "sync"
cur = defaultdict(float)          # per-frame accumulators
cnt = defaultdict(int)
lock = threading.Lock()


def _sync():
    torch.cuda.synchronize()


def wrap_callable(fn, label, gpu=False):
    def w(*a, **k):
        if SYNC and gpu:
            _sync()
        t0 = time.perf_counter()
        r = fn(*a, **k)
        if SYNC and gpu:
            _sync()
        dt = (time.perf_counter() - t0) * 1000.0
        with lock:
            cur[label] += dt
            cnt[label] += 1
        return r
    return w


def wrap_attr(obj, name, label, gpu=False):
    setattr(obj, name, wrap_callable(getattr(obj, name), label, gpu))


gp = proc._gpu_pipeline
wrap_attr(gp, "_upload_to_gpu", "g_upload", gpu=True)
wrap_attr(gp._enhancer, "enhance", "g_enhance", gpu=True)
wrap_attr(gp, "_prepare_yolo_input", "g_letterbox", gpu=True)
wrap_attr(gp._resizer, "resize", "g_preview_resize", gpu=True)
import core.gpu_pipeline as GPM  # noqa: E402
GPM.GpuFrame.to_numpy_bgr = wrap_callable(GPM.GpuFrame.to_numpy_bgr, "g_preview_download", gpu=True)

# --- YOLO: total + ultralytics internals --------------------------------
import ultralytics.utils.ops as UOPS  # noqa: E402
import ultralytics.data.loaders as ULD  # noqa: E402
UOPS.convert_torch2numpy_batch = wrap_callable(UOPS.convert_torch2numpy_batch, "y_origimg_d2h", gpu=False)
_orig_check = ULD.LoadTensor._single_check
ULD.LoadTensor._single_check = staticmethod(wrap_callable(_orig_check, "y_tensor_check_max", gpu=False))

_model = proc.model
speeds = defaultdict(float)


class TimedModel:
    def __init__(self, m):
        self._m = m

    def __getattr__(self, n):
        return getattr(self._m, n)

    def __call__(self, *a, **k):
        if SYNC:
            _sync()
        t0 = time.perf_counter()
        r = self._m(*a, **k)
        if SYNC:
            _sync()
        dt = (time.perf_counter() - t0) * 1000.0
        with lock:
            cur["yolo_call"] += dt
            for kk, vv in (r[0].speed or {}).items():
                cur[f"y_{kk}"] += float(vv or 0.0)
        return r


proc.model = TimedModel(_model)

wrap_attr(proc, "_extract_detections", "extract_d2h")
wrap_attr(proc, "_filter_duplicate_detections", "dedup")
if args.serial_motion:
    def _inline_submit(gray):
        proc._feed_motion_detectors(gray)
        proc._motion_future = None
    proc._submit_motion_feed = _inline_submit
wrap_attr(proc, "_submit_motion_feed", "motion_submit")
wrap_attr(proc, "_await_motion_feed", "motion_wait")
wrap_attr(proc, "_post_yolo_chain", "post_yolo")
wrap_attr(proc, "_crossval_motion_filter", "pc_crossval")
wrap_attr(proc, "_exclusion_step", "pc_exclusion")
wrap_attr(proc, "_gate_cold_blobs", "pc_cold_gate")
wrap_attr(proc, "_unscale_letterbox", "pc_finalize")
wrap_attr(proc, "_smooth_output_box_sizes", "pc_box_ema")
wrap_attr(proc.tracker, "update", "pc_tracker_update")
wrap_attr(proc.tracker.logger, "flush", "logger_flush")
if args.osc:
    wrap_attr(proc.osc, "send_frame", "pc_osc_send")

# Motion worker sub-stages (run on the mog2-feed thread; CPU only)
wrap_attr(proc, "_feed_motion_detectors", "mw_total")
wrap_attr(proc, "_fixed_gamma_for_motion", "mw_gamma_lut")
mm = proc.motion_model
wrap_attr(mm, "_accumulate_noise", "mw_noise_welford")
import core.motion_detector as MDM  # noqa: E402
_pre = MDM.MotionDetector.preprocess
MDM.MotionDetector.preprocess = staticmethod(wrap_callable(_pre, "mw_preprocess_blur_resize"))
wrap_attr(mm._det, "feed_preprocessed", "mw_feed_pre(diff+mog2+morph)")
wrap_attr(mm._det, "detect", "pc_blob_detect")

# --- optional cProfile of the CPU post-YOLO chain -------------------------
prof = None
if args.profile_post:
    import cProfile
    prof = cProfile.Profile()
    _ppc = proc._post_yolo_chain

    def _profiled(*a, **k):
        prof.enable()
        try:
            return _ppc(*a, **k)
        finally:
            prof.disable()
    proc._post_yolo_chain = _profiled

# --- GPU monitor -----------------------------------------------------------
import subprocess  # noqa: E402
smi_path = str(logdir / "nvidia_smi.csv")
smi = subprocess.Popen(
    ["nvidia-smi", "--query-gpu=timestamp,utilization.gpu,clocks.sm,power.draw,memory.used,temperature.gpu",
     "--format=csv,noheader,nounits", "-lms", "250"],
    stdout=open(smi_path, "w"), stderr=subprocess.DEVNULL)
apps0 = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                        "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()
load0 = os.getloadavg()

cap = cv2.VideoCapture(str(video))
cap.set(cv2.CAP_PROP_POS_FRAMES, start)
rows = []
dec_ms = []
t_run0 = None
for i in range(args.frames + args.warmup):
    t0 = time.perf_counter()
    ok, frame = cap.read()
    dec_ms.append((time.perf_counter() - t0) * 1000.0)
    if not ok:
        break
    cur.clear()
    if i == args.warmup:
        t_run0 = time.perf_counter()
    t0 = time.perf_counter()
    tracks, prev, timing, lat = proc.process(frame, need_preview=args.preview, frame_number=i)
    wall = (time.perf_counter() - t0) * 1000.0
    if i < args.warmup:
        continue
    row = dict(cur)
    row["process_wall"] = wall
    row["n_tracks"] = len(tracks)
    for k in ("yolo", "extract", "mog2_cvt", "mog2_feed", "tracker_update", "track", "enhance",
              "yolo_resize", "upload", "total", "preview_download", "preview_resize"):
        v = timing.get(k)
        if isinstance(v, (int, float)):
            row[f"app.{k}"] = float(v)
    rows.append(row)
t_run = time.perf_counter() - t_run0
cap.release()
proc.tracker.logger.close()
smi.terminate()
load1 = os.getloadavg()

if prof is not None:
    prof.dump_stats(args.profile_post + ".prof")


def stats(v):
    a = np.asarray(v, dtype=np.float64)
    return {"mean": round(float(a.mean()), 3), "p50": round(float(np.percentile(a, 50)), 3),
            "p95": round(float(np.percentile(a, 95)), 3), "max": round(float(a.max()), 3),
            "n": int(a.size)}


keys = sorted({k for r in rows for k in r})
out = {"args": vars(args), "video": str(video), "frames": len(rows),
       "run_s": round(t_run, 2), "loop_fps_excl_decode": None,
       "stages": {}, "decode_ms": stats(dec_ms[args.warmup:]),
       "load_before": load0, "load_after": load1, "gpu_apps_at_start": apps0,
       "cv2_threads": cv2.getNumThreads(), "torch_threads": torch.get_num_threads()}
for k in keys:
    vals = [r.get(k, 0.0) for r in rows]
    if k == "n_tracks":
        out["stages"][k] = stats(vals)
        continue
    out["stages"][k] = stats(vals)
out["loop_fps_excl_decode"] = round(1000.0 / out["stages"]["process_wall"]["mean"], 2)
# GPU utilisation during the run
try:
    sm = np.genfromtxt(smi_path, delimiter=",", usecols=(1, 2, 3, 4, 5))
    sm = np.atleast_2d(sm)
    out["nvidia_smi"] = {"util_mean": round(float(np.nanmean(sm[:, 0])), 1),
                         "util_p95": round(float(np.nanpercentile(sm[:, 0], 95)), 1),
                         "sm_clock_mean": round(float(np.nanmean(sm[:, 1])), 0),
                         "power_mean_w": round(float(np.nanmean(sm[:, 2])), 1),
                         "mem_used_max_mib": round(float(np.nanmax(sm[:, 3])), 0),
                         "temp_max": round(float(np.nanmax(sm[:, 4])), 0),
                         "samples": int(sm.shape[0])}
except Exception as e:  # noqa: BLE001
    out["nvidia_smi"] = {"error": str(e)}
Path(args.out).write_text(json.dumps(out, indent=1))
print(json.dumps({k: out[k] for k in ("frames", "run_s", "loop_fps_excl_decode", "nvidia_smi", "cv2_threads")}))
for k in ("process_wall", "yolo_call", "y_preprocess", "y_inference", "y_postprocess", "y_origimg_d2h",
          "g_upload", "g_enhance", "g_letterbox", "extract_d2h", "motion_wait", "mw_total", "post_yolo",
          "pc_tracker_update", "logger_flush"):
    if k in out["stages"]:
        s = out["stages"][k]
        print(f"  {k:32s} mean {s['mean']:8.2f}  p50 {s['p50']:8.2f}  p95 {s['p95']:8.2f}  max {s['max']:8.2f}")
