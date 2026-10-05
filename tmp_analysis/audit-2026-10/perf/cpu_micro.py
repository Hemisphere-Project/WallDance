"""CPU micro-benchmarks for the WallDance per-frame CPU work (perf audit, scratch).

Every op is timed on REAL frames from hangar-aerial (slot 4, frames 1600..) at
the scenario's ROI (1296x1195), the recorded source (1488x1528) and a
hypothetical full-sensor 4 MP mono frame (2560x1600 upscaled), with
cv2.setNumThreads 1 (what the app runs with: `import ultralytics` calls
cv2.setNumThreads(0)) vs 8.

Run: .venv/bin/python cpu_micro.py --out cpu_micro.json
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, "/data/WallDance/application/src")
ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--reps", type=int, default=40)
args = ap.parse_args()

VIDEO = "/data/WallDance/projects/residence1-solo/recordings/slot_4_20260402_210914.avi"
cap = cv2.VideoCapture(VIDEO)
cap.set(cv2.CAP_PROP_POS_FRAMES, 1600)
frames = []
for _ in range(6):
    ok, f = cap.read()
    frames.append(f)
cap.release()
ROI = (127, 228, 1296, 1195)
x, y, w, h = ROI


def bench(fn, reps=args.reps, warm=3):
    for _ in range(warm):
        fn()
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    a = np.asarray(ts)
    return {"min": round(float(a.min()), 3), "p10": round(float(np.percentile(a, 10)), 3),
            "p50": round(float(np.median(a)), 3), "mean": round(float(a.mean()), 3),
            "p95": round(float(np.percentile(a, 95)), 3)}


res = {"load_before": os.getloadavg()}
bgr_full = frames[0]
bgr_roi = np.ascontiguousarray(bgr_full[y:y + h, x:x + w])
gray_roi = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2GRAY)
gray_full = cv2.cvtColor(bgr_full, cv2.COLOR_BGR2GRAY)
gray_4mp = cv2.resize(gray_full, (2560, 1600), interpolation=cv2.INTER_LINEAR)
gray_roi2 = cv2.cvtColor(np.ascontiguousarray(frames[1][y:y + h, x:x + w]), cv2.COLOR_BGR2GRAY)
lut = np.array([((i / 255.0) ** (1 / 2.2)) * 255 for i in range(256)], dtype=np.uint8)

for nthreads in (1, 8):
    cv2.setNumThreads(nthreads)
    R = {}
    for name, g, g2 in (("roi_1296x1195", gray_roi, gray_roi2),
                        ("src_1488x1528", gray_full, None),
                        ("4mp_2560x1600", gray_4mp, None)):
        bgr = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        r = {}
        r["cvtColor_BGR2GRAY"] = bench(lambda: cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        r["mono_channel0_contig(P-1)"] = bench(lambda: np.ascontiguousarray(bgr[:, :, 0]))
        r["cvtColor_GRAY2BGR"] = bench(lambda: cv2.cvtColor(g, cv2.COLOR_GRAY2BGR))
        r["LUT_gamma"] = bench(lambda: cv2.LUT(g, lut))
        r["mean_brightness"] = bench(lambda: float(np.mean(g)))
        r["median5_fullres"] = bench(lambda: cv2.medianBlur(g, 5))
        r["gauss5_fullres"] = bench(lambda: cv2.GaussianBlur(g, (5, 5), 0))
        r["gauss3_fullres"] = bench(lambda: cv2.GaussianBlur(g, (3, 3), 0))
        r["resize_area_0.7"] = bench(lambda: cv2.resize(g, None, fx=0.7, fy=0.7, interpolation=cv2.INTER_AREA))
        small = cv2.resize(g, None, fx=0.7, fy=0.7, interpolation=cv2.INTER_AREA)
        r["median5+gauss5_on_0.7(P-4)"] = bench(lambda: cv2.GaussianBlur(cv2.medianBlur(small, 5), (5, 5), 0))
        r["absdiff_max_fullres(diff_scale=1.0)"] = bench(lambda: int(cv2.absdiff(g, g).max()))
        g05 = cv2.resize(g, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        r["resize_area_0.5+absdiff_max(P-3)"] = bench(
            lambda: int(cv2.absdiff(cv2.resize(g, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA), g05).max()))
        # MOG2 at scale 0.7 (pinned config), shadows on, history 500
        mog = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=8, detectShadows=True)
        for _ in range(10):
            mog.apply(small, learningRate=0.001)
        r["mog2_apply_0.7"] = bench(lambda: mog.apply(small, learningRate=0.001))
        mask = mog.apply(small, learningRate=0.001)
        k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        r["clean_mask(==255,open3)"] = bench(
            lambda: cv2.morphologyEx((mask == 255).astype(np.uint8) * 255, cv2.MORPH_OPEN, k3))
        # Welford noise accumulator as in MotionModel._accumulate_noise
        st = {"n": 1, "m": small.astype(np.float32), "m2": np.zeros(small.shape, np.float32)}

        def welford():
            xx = small.astype(np.float32)
            st["n"] += 1
            d = xx - st["m"]
            st["m"] += d / st["n"]
            st["m2"] += d * (xx - st["m"])
        r["welford_noise_0.7"] = bench(welford)
        # findContours-based blob detect on the cleaned mask
        def detect():
            fg = (mask >= 127).astype(np.uint8) * 255
            fg = cv2.erode(fg, k3)
            fg = cv2.dilate(fg, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
            return cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        r["blob_detect_contours_0.7"] = bench(detect)
        # IR-marker stage candidates (threshold + CC) on the full gray
        r["marker_thresh+CCstats"] = bench(
            lambda: cv2.connectedComponentsWithStats(cv2.threshold(g, 245, 255, cv2.THRESH_BINARY)[1], connectivity=8))
        r["marker_thresh+CCstats_on_half"] = bench(
            lambda: cv2.connectedComponentsWithStats(
                cv2.threshold(cv2.resize(g, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA), 245, 255,
                              cv2.THRESH_BINARY)[1], connectivity=8))
        # sparse variant: find bright pixels first, CC only on bounding windows
        def sparse():
            m = cv2.threshold(g, 245, 255, cv2.THRESH_BINARY)[1]
            nz = cv2.findNonZero(m)
            return nz
        r["marker_thresh+findNonZero"] = bench(sparse)
        R[name] = r
    res[f"cv2_threads_{nthreads}"] = R

# --- whole motion feed as the app runs it (single-thread cv2) ---------------
from core.motion_model import MotionModel  # noqa: E402
for nthreads in (1, 8):
    cv2.setNumThreads(nthreads)
    mm = MotionModel()
    mm.set_scale(0.7)
    mm.set_var_threshold(8)
    seq = []
    cap = cv2.VideoCapture(VIDEO)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 1500)
    for _ in range(110):
        ok, f = cap.read()
        seq.append(cv2.LUT(cv2.cvtColor(np.ascontiguousarray(f[y:y + h, x:x + w]), cv2.COLOR_BGR2GRAY), lut))
    cap.release()
    for gfr in seq[:10]:
        mm.feed(gfr)
    ts = []
    for gfr in seq[10:]:
        t0 = time.perf_counter()
        mm.feed(gfr)
        ts.append((time.perf_counter() - t0) * 1000)
    a = np.asarray(ts)
    res[f"motion_model_feed_roi_threads{nthreads}"] = {"min": round(float(a.min()), 3), "p10": round(float(np.percentile(a, 10)), 3),
                                                       "p50": round(float(np.median(a)), 3), "mean": round(float(a.mean()), 3),
                                                       "p95": round(float(np.percentile(a, 95)), 3)}

# --- IDS path (CPU side): Mono10g40 unpack + cache BGR ----------------------
from camera.ids_camera import IDSCamera  # noqa: E402
W, H = 1528, 1528
raw = np.random.randint(0, 255, size=(W * H * 5 // 4,), dtype=np.uint8)
cv2.setNumThreads(1)
res["ids_raw_copy_mono10g40_1528"] = bench(lambda: raw.copy())
res["ids_unpack_mono10g40_1528"] = bench(lambda: IDSCamera._unpack_raw(raw, W, H, "mono10g40ids"))
mono = IDSCamera._unpack_raw(raw, W, H, "mono10g40ids")
res["ids_gray2bgr_cache_1528"] = bench(lambda: cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR))
bgr = cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)
res["ids_frame_copy_bgr_1528 (recorder/_last_review copies)"] = bench(lambda: bgr.copy())
W4, H4 = 2560, 1600
raw4 = np.random.randint(0, 255, size=(W4 * H4 * 5 // 4,), dtype=np.uint8)
res["ids_unpack_mono10g40_4mp"] = bench(lambda: IDSCamera._unpack_raw(raw4, W4, H4, "mono10g40ids"))

# --- preview / GUI CPU path -------------------------------------------------
cv2.setNumThreads(1)
rw, rh = int(1488 * 0.6835), int(1528 * 0.6835)
roi_prev = bgr_roi.copy()


def compose_and_resize():
    canvas = np.zeros((1528, 1488, 3), dtype=np.uint8)
    canvas[y:y + h, x:x + w] = roi_prev
    return cv2.resize(canvas, (rw, rh))
res["preview_compose_roi+resize"] = bench(compose_and_resize)
pf = compose_and_resize()
fb = np.zeros(rw * rh * 4, dtype=np.float32)


def gui_update_frame():
    rgba = cv2.cvtColor(pf, cv2.COLOR_BGR2RGBA)
    np.multiply(rgba.reshape(-1), np.float32(1.0 / 255.0), out=fb)
res[f"gui_update_frame_cvt+float_{rw}x{rh}"] = bench(gui_update_frame)
res["web_monitor_update_copy"] = bench(lambda: pf.copy())
res["web_monitor_jpeg_q70"] = bench(lambda: cv2.imencode(".jpg", pf, [cv2.IMWRITE_JPEG_QUALITY, 70]))
from core.visualization import draw_dancer  # noqa: E402
from core.pipeline import ScaledTrack  # noqa: E402
kp = np.random.rand(17, 2) * 300 + 200
st = ScaledTrack(track_id=1, keypoints=kp, confidence=np.ones(17) * 0.9,
                 bbox=np.array([200, 200, 120, 300.0]), history=[], velocity=np.zeros(2))
res["draw_dancer_x1"] = bench(lambda: draw_dancer(pf, st, show_skeleton=True, show_keypoints=False,
                                                  show_bbox=True, show_trail=False, show_id=True))

# --- OSC send_frame (python-osc, UDP to closed port) -------------------------
from core.osc_output import OSCSender  # noqa: E402
osc = OSCSender("127.0.0.1", 59999)
for n in (1, 4):
    tr = [st] * n
    res[f"osc_send_frame_{n}_dancers"] = bench(lambda: osc.send_frame(tr, 1488, 1528), reps=200)

# --- recording encoders (per frame, encoder thread) --------------------------
import tempfile  # noqa: E402
tmpd = tempfile.mkdtemp(dir=str(Path(args.out).parent))
for nthreads in (1, 8):
    cv2.setNumThreads(nthreads)
    for codec, ext, color in (("FFV1", ".avi", True), ("FFV1", ".avi", False), ("MJPG", ".avi", True)):
        p = os.path.join(tmpd, f"t_{codec}_{color}_{nthreads}{ext}")
        wr = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*codec), 20.0, (1488, 1528), isColor=color)
        src = bgr_full if color else gray_full
        res[f"rec_{codec}_{'bgr' if color else 'gray'}_1488x1528_cvthreads{nthreads}"] = bench(
            lambda: wr.write(src), reps=20)
        wr.release()
        try:
            os.remove(p)
        except OSError:
            pass
# FFV1 decode (playback / replay)
cv2.setNumThreads(1)
cap = cv2.VideoCapture(VIDEO)
cap.set(cv2.CAP_PROP_POS_FRAMES, 1700)
res["ffv1_decode_bgr_1488x1528"] = bench(lambda: cap.read(), reps=30)
cap.release()
res["load_after"] = os.getloadavg()
Path(args.out).write_text(json.dumps(res, indent=1))
print(json.dumps(res, indent=1))
