"""CPU micro-benchmarks of the motion feed (2026-11 audit; cv2 threads = the app's 4)."""
import sys, time
from pathlib import Path
APP = Path("/data/WallDance/application"); sys.path.insert(0, str(APP / "src"))
import numpy as np, cv2
import core.pipeline  # imports ultralytics, then restores cv2 threads (PERF-1)
from core.motion_model import MotionModel
from core.motion_detector import MotionDetector
print("cv2 threads:", cv2.getNumThreads())

def load(path, start, n, roi):
    cap = cv2.VideoCapture(path); cap.set(cv2.CAP_PROP_POS_FRAMES, start); out = []
    x, y, w, h = roi
    for _ in range(n):
        ok, f = cap.read()
        if not ok: break
        out.append(np.ascontiguousarray(f[y:y+h, x:x+w, 0]))
    cap.release(); return out

def tm(fn, frames, reps=3):
    ts = []
    for _ in range(reps):
        for g in frames:
            t0 = time.perf_counter(); fn(g); ts.append((time.perf_counter() - t0) * 1e3)
    a = np.array(ts); return f"min {a.min():6.2f}  p10 {np.percentile(a,10):6.2f}  p50 {np.percentile(a,50):6.2f}"

lut = np.array([((i / 255.0) ** (1/2.2)) * 255 for i in range(256)], dtype=np.uint8)
cases = {
 "aerial ROI 1296x1195 (dark: low-light branch), scale 0.7": ("/data/WallDance/projects/3_TANGO_HANGAR-whitebg2/recordings/slot_4_20260402_210914.avi", 1500, 30, (127, 228, 1296, 1195), 0.7),
 "aerial strip 1304x566, scale 0.7": ("/data/WallDance/projects/3_TANGO_HANGAR-whitebg2/recordings/slot_4_20260402_210914.avi", 1500, 30, (92, 401, 1304, 566), 0.7),
 "aerial ROI 1296x1195, scale 0.5": ("/data/WallDance/projects/3_TANGO_HANGAR-whitebg2/recordings/slot_4_20260402_210914.avi", 1500, 30, (127, 228, 1296, 1195), 0.5),
}
for name, (path, start, n, roi, scale) in cases.items():
    raw = load(path, start, n, roi)
    fr = [cv2.LUT(g, lut) for g in raw]
    print(f"== {name}; brightness after gamma {np.mean(fr[0]):.1f}")
    print("  gamma LUT                               ", tm(lambda g: cv2.LUT(g, lut), raw))
    print("  np.mean brightness                      ", tm(lambda g: float(np.mean(g)), fr))
    print("  median5 full res                        ", tm(lambda g: cv2.medianBlur(g, 5), fr))
    print("  gauss5 full res                         ", tm(lambda g: cv2.GaussianBlur(g, (5, 5), 0), fr))
    print(f"  resize INTER_AREA x{scale}                  ", tm(lambda g: cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), fr))
    print("  preprocess NOW (mean+median5+gauss5+AREA)", tm(lambda g: MotionDetector.preprocess(g, scale), fr))
    def pre_after(g):
        s = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(cv2.medianBlur(s, 5), (5, 5), 0)
    print("  variant: AREA first, then median5+gauss5 ", tm(pre_after, fr))
    def pre_lin(g):
        s = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        return cv2.GaussianBlur(cv2.medianBlur(s, 5), (5, 5), 0)
    print("  variant: LINEAR first, then median5+gauss5", tm(pre_lin, fr))
    mm = MotionModel(scale=scale, var_threshold=8)
    for g in fr: mm.feed(g)
    smalls = [MotionDetector.preprocess(g, scale)[0] for g in fr]
    print("  Welford accumulate                      ", tm(mm._accumulate_noise, smalls))
    it = iter(range(10**9))
    def fp(g):
        i = next(it) % len(fr)
        mm._det.feed_preprocessed(smalls[i], 40.0, raw_gray=fr[i])
    print("  feed_preprocessed (absdiff max full-res + MOG2 + open)", tm(fp, fr))
    mog = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=16, detectShadows=True)
    for s_ in smalls: mog.apply(s_, learningRate=0.001)
    print("    of which MOG2.apply                   ", tm(lambda s_: mog.apply(s_, learningRate=0.001), smalls))
    print("    of which absdiff().max() full res     ", tm(lambda g: int(cv2.absdiff(g, fr[0]).max()), fr))
    print("  WHOLE MotionModel.feed                   ", tm(mm.feed, fr))
