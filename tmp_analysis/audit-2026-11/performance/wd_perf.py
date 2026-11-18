"""Per-stage budget harness (2026-11 audit, scratch only; derived from
tmp_analysis/audit-2026-10/perf/perf_harness.py).  Drives the REAL FrameProcessor built by
tests/replay.py over a scenario and wraps stages with timers on the instances (no repo
edits, every output under --out's folder).

  natural : no extra syncs (what the app pays)
  sync    : torch.cuda.synchronize() around GPU stages (true GPU attribution)
  --ids   : emulate the IDS path (mono8 -> read_gpu ops -> process_gpu_direct)
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

import replay  # noqa: E402
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--scenario", required=True)
ap.add_argument("--model", default="yolo11x-pose")
ap.add_argument("--imgsz", type=int, default=1280)
ap.add_argument("--mode", default="natural", choices=["natural", "sync"])
ap.add_argument("--frames", type=int, default=300)
ap.add_argument("--warmup", type=int, default=20)
ap.add_argument("--preview", action="store_true")
ap.add_argument("--ids", action="store_true")
ap.add_argument("--plate", action="store_true", help="take-median clean plate -> foreground on")
ap.add_argument("--profile-post", default=None)
ap.add_argument("--profile-worker", default=None)
ap.add_argument("--set", dest="sets", action="append", default=[])
ap.add_argument("--out", required=True)
args = ap.parse_args()

manifest = json.loads(Path(args.scenario).read_text())
config = replay.scenario_config(manifest)
import scoring  # noqa: E402
config.setdefault("max_dancers", max(1, scoring.max_expected(scoring.load_scenario(args.scenario))))
replay.apply_overrides(config, args.sets)
config, filled = replay.fill_detection_defaults(config)
video = replay._find_recording(manifest["project"], manifest["slot"])
start = manifest["start"]
fps = float(manifest.get("fps") or 20.0)

proc = replay._build_processor(config, args.model, args.imgsz, use_gpu_path=True, use_trt=True)
proc.tracker.reset()
clock = replay._attach_frame_clock(proc, fps)
outp = Path(args.out)
logdir = Path(str(outp.with_suffix("")) + "_log")
logdir.mkdir(parents=True, exist_ok=True)
proc.tracker.logger.start_session(str(logdir))
from core.osc_output import OSCSender  # noqa: E402
proc.attach_osc(OSCSender("127.0.0.1", 59999))
proc.settings.osc_enabled = True
if args.preview:
    proc.set_preview_fps_cap(15.0)
    proc.set_preview_size(int(1488 * config.get("preview_scale", 0.7)),
                          int(1528 * config.get("preview_scale", 0.7)))

SYNC = args.mode == "sync"
cur = defaultdict(float)
cnt = defaultdict(int)
lock = threading.Lock()


def wrap_callable(fn, label, gpu=False):
    def w(*a, **k):
        if SYNC and gpu:
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        r = fn(*a, **k)
        if SYNC and gpu:
            torch.cuda.synchronize()
        dt = (time.perf_counter() - t0) * 1000.0
        with lock:
            cur[label] += dt
            cnt[label] += 1
        return r
    return w


def wrap_attr(obj, name, label, gpu=False):
    if hasattr(obj, name):
        setattr(obj, name, wrap_callable(getattr(obj, name), label, gpu))
    else:
        print(f"[harness] no attribute {name} on {type(obj).__name__}", file=sys.stderr)


gp = proc._gpu_pipeline
wrap_attr(gp, "_upload_to_gpu", "g_upload", gpu=True)
wrap_attr(gp._enhancer, "enhance", "g_enhance", gpu=True)
wrap_attr(gp, "_prepare_yolo_input", "g_letterbox", gpu=True)
wrap_attr(gp._resizer, "resize", "g_preview_resize", gpu=True)
import core.gpu_pipeline as GPM  # noqa: E402
GPM.GpuFrame.to_numpy_bgr = wrap_callable(GPM.GpuFrame.to_numpy_bgr, "g_preview_download", gpu=True)

wrap_attr(proc, "_run_yolo", "yolo_run", gpu=True)
wrap_attr(proc, "_extract_detections", "extract_d2h")
wrap_attr(proc, "_guard_person_height", "height_guard")
wrap_attr(proc, "_filter_duplicate_detections", "dedup")
wrap_attr(proc, "_start_motion_feed", "motion_start(crop+cvt+submit)")
wrap_attr(proc, "_await_motion_feed", "motion_wait")
wrap_attr(proc, "_post_yolo_chain", "post_yolo")
wrap_attr(proc, "_crossval_motion_filter", "pc_crossval")
wrap_attr(proc, "_exclusion_step", "pc_exclusion")
wrap_attr(proc, "_gate_cold_blobs", "pc_cold_gate")
wrap_attr(proc, "_unscale_letterbox", "pc_finalize")
wrap_attr(proc, "_smooth_output_box_sizes", "pc_box_ema")
wrap_attr(proc, "_run_identity_slots", "pc_slots_total")
wrap_attr(proc, "_belt_hook", "pc_belt_hook_build")
wrap_attr(proc, "_fg_frame", "pc_fg_frame")
wrap_attr(proc, "_emit", "pc_emit")
wrap_attr(proc, "_log_slots", "pc_log_slots")
wrap_attr(proc, "_learn_person_height", "pc_auto_height")
wrap_attr(proc.osc, "send_frame", "pc_osc_send")
trk = proc.tracker
wrap_attr(trk, "update", "pc_tracker_update")
for name, label in [
        ("_begin_frame_update", "t_begin"),
        ("_predict_tracks_for_frame", "t_predict"),
        ("_fuse_motion_blobs", "t_fuse_blobs"),
        ("_run_matching_phase", "t_matching"),
        ("_compute_cost_matrix", "t_cost_matrix"),
        ("_resolve_unmatched_detections", "t_unmatched"),
        ("_lazy_bridge_with_motion", "t_lazy_bridge"),
        ("_bridge_with_local_motion_support", "t_bridge_local"),
        ("_apply_occlusion_aging", "t_occl_aging"),
        ("_finalize_track_lifecycle", "t_lifecycle"),
        ("_collect_confirmed_tracks", "t_collect"),
        ("_log_frame_summary", "t_log_summary"),
        ("_update_pair_distances", "t_pair_dist"),
        ("_detect_shadow_tracks", "t_shadow"),
        ("_merge_duplicate_tracks", "t_merge_dup"),
        ("_merge_takeover_duplicates", "t_merge_takeover")]:
    wrap_attr(trk, name, label)
wrap_attr(trk.logger, "log", "t_logger_log")
wrap_attr(trk.logger, "flush", "logger_flush")

wrap_attr(proc, "_feed_motion_detectors", "mw_total")
wrap_attr(proc, "_fixed_gamma_for_motion", "mw_gamma_lut")
mm = proc.motion_model
wrap_attr(mm, "_accumulate_noise", "mw_noise_welford")
import core.motion_detector as MDM  # noqa: E402
MDM.MotionDetector.preprocess = staticmethod(
    wrap_callable(MDM.MotionDetector.preprocess, "mw_preprocess_blur_resize"))
det = mm._det
wrap_attr(det, "feed_preprocessed", "mw_feed_pre(diff+mog2+morph)")
wrap_attr(det, "detect", "md_detect(global blobs)")
for name in ("frame_diff_blob_in_bbox", "extract_local_motion_blob", "motion_ratio_in_bbox",
             "mask_stats_in_region"):
    wrap_attr(det, name, f"md_{name}")
_cc = cv2.connectedComponentsWithStats
cv2.connectedComponentsWithStats = wrap_callable(_cc, "cv_connectedComponents")
_fc = cv2.findContours
cv2.findContours = wrap_callable(_fc, "cv_findContours")

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

# --- IDS emulation: what IDSCamera.read_gpu does on the main thread ----------
_ids = {"pinned": None, "stream": torch.cuda.Stream() if args.ids else None}


def ids_read_gpu(mono):
    t = torch.from_numpy(mono)
    if _ids["pinned"] is None or _ids["pinned"].shape != t.shape:
        _ids["pinned"] = torch.empty_like(t).pin_memory()
    _ids["pinned"].copy_(t)
    with torch.cuda.stream(_ids["stream"]):
        g = _ids["pinned"].cuda(non_blocking=True)
    _ids["stream"].synchronize()
    f = g.float().mul_(1.0 / 255.0).clamp_(0.0, 1.0)
    gpu = f.unsqueeze(0).unsqueeze(0).expand(1, 3, -1, -1).contiguous()
    bgr = cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)
    return gpu, bgr


import subprocess  # noqa: E402
smi_path = str(logdir / "nvidia_smi.csv")
smi = subprocess.Popen(
    ["nvidia-smi", "--query-gpu=timestamp,utilization.gpu,clocks.sm,power.draw,memory.used,temperature.gpu",
     "--format=csv,noheader,nounits", "-lms", "250"],
    stdout=open(smi_path, "w"), stderr=subprocess.DEVNULL)
load0 = os.getloadavg()

cap = cv2.VideoCapture(str(video))
cap.set(cv2.CAP_PROP_POS_FRAMES, start)

if args.plate:
    # take-median plate over the run's span (stride 8) -> foreground hook active
    from core.foreground import CleanPlate
    fr = []
    n_span = args.frames + args.warmup
    for i in range(n_span):
        ok, f = cap.read()
        if not ok:
            break
        if i % 8 == 0:
            fr.append(np.ascontiguousarray(f[:, :, 0]))
    proc.set_clean_plate(CleanPlate.from_frames(fr, source="take-median"))
    proc.settings.fg_enabled = True
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)

rows = []
t_run0 = None
for i in range(args.frames + args.warmup):
    ok, frame = cap.read()
    if not ok:
        break
    cur.clear()
    cnt.clear()
    clock["frame"] = i
    if i == args.warmup:
        t_run0 = time.perf_counter()
    t0 = time.perf_counter()
    if args.ids:
        mono = np.ascontiguousarray(frame[:, :, 0])      # acquisition thread's product
        t0 = time.perf_counter()
        gpu, bgr = ids_read_gpu(mono)
        t_read = (time.perf_counter() - t0) * 1000.0
        tracks, prev, timing, lat = proc.process_gpu_direct(
            gpu, need_preview=args.preview, frame_number=i, raw_frame=bgr)
    else:
        t_read = 0.0
        tracks, prev, timing, lat = proc.process(frame, need_preview=args.preview, frame_number=i)
    wall = (time.perf_counter() - t0) * 1000.0
    if i < args.warmup:
        continue
    row = dict(cur)
    for k, v in cnt.items():
        row[f"n.{k}"] = v
    row["frame_wall"] = wall
    row["camera_read(ids emu)"] = t_read
    row["n_tracks"] = len(tracks)
    row["n_emitted"] = len(getattr(proc, "last_emitted", []) or [])
    for k in ("yolo", "extract", "mog2_cvt", "mog2_feed", "mog2_wait", "tracker_update", "track",
              "enhance", "yolo_resize", "upload", "total", "preview_download", "preview_resize", "fg"):
        v = timing.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
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
    return {"mean": round(float(a.mean()), 3), "min": round(float(a.min()), 3),
            "p10": round(float(np.percentile(a, 10)), 3),
            "p50": round(float(np.percentile(a, 50)), 3),
            "p95": round(float(np.percentile(a, 95)), 3), "max": round(float(a.max()), 3)}


keys = sorted({k for r in rows for k in r})
out = {"args": vars(args), "video": str(video), "frames": len(rows), "run_s": round(t_run, 2),
       "config": {k: config.get(k) for k in ("confidence", "tracking_mode", "tracker_intermittent_confirm",
                                             "mog2_scale", "mog2_var_threshold", "gamma", "clahe_clip",
                                             "roi_w", "roi_h", "person_height_px", "max_dancers")},
       "pose_runner": {"fast": proc._yolo_runner.fast_calls, "regular": proc._yolo_runner.regular_calls,
                       "enabled": proc._yolo_runner.enabled},
       "stages": {k: stats([r.get(k, 0.0) for r in rows]) for k in keys},
       "load_before": load0, "load_after": load1,
       "cv2_threads": cv2.getNumThreads(), "torch_threads": torch.get_num_threads()}
try:
    sm = np.atleast_2d(np.genfromtxt(smi_path, delimiter=",", usecols=(1, 2, 3, 4, 5)))
    out["nvidia_smi"] = {"util_mean": round(float(np.nanmean(sm[:, 0])), 1),
                         "sm_clock_mean": round(float(np.nanmean(sm[:, 1])), 0),
                         "power_mean_w": round(float(np.nanmean(sm[:, 2])), 1),
                         "temp_max": round(float(np.nanmax(sm[:, 4])), 0)}
except Exception as e:  # noqa: BLE001
    out["nvidia_smi"] = {"error": str(e)}
outp.write_text(json.dumps(out, indent=1))
print(json.dumps({k: out[k] for k in ("frames", "run_s", "config", "pose_runner", "nvidia_smi",
                                      "cv2_threads", "torch_threads", "load_before", "load_after")}))
for k in keys:
    s = out["stages"][k]
    if k.startswith("n."):
        if s["mean"] > 0:
            print(f"  {k:44s} mean {s['mean']:7.2f}  p95 {s['p95']:6.1f} max {s['max']:6.1f}")
        continue
    print(f"  {k:44s} min {s['min']:7.2f} p10 {s['p10']:7.2f} p50 {s['p50']:7.2f} "
          f"mean {s['mean']:7.2f} p95 {s['p95']:7.2f} max {s['max']:7.2f}")
