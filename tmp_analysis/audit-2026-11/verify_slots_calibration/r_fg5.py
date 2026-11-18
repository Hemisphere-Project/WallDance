"""identity-output#5 (stale plate is silent) and #7 (age of a never-used slot)."""
import sys, time
sys.dont_write_bytecode = True
import numpy as np
from core.foreground import CleanPlate, ForegroundDetector
from core.ops_monitor import light_out_of_band, check_empty_wall

H, W = 600, 1400
rng = np.random.default_rng(1)
wall = np.clip(40 + 6 * rng.normal(0, 1, (H // 20, W // 20)).repeat(20, 0).repeat(20, 1), 5, 250)
def frame(shift=0, scale=1.0, sigma=1.0):
    f = np.roll(wall, shift, axis=1) * scale
    return np.clip(f + rng.normal(0, sigma, f.shape), 0, 255).astype(np.uint8)

plate = CleanPlate.from_frames([frame() for _ in range(40)], source="synthetic")
det = ForegroundDetector(plate)
for label, kw in (("same scene", {}), ("camera nudged 12 px sideways, same light", {"shift": 12}),
                  ("light x0.8 (inside the 1.5x alert band), same framing", {"scale": 0.8})):
    valid = 0
    for _ in range(100):
        f = frame(**kw)
        fg = det.process(f, 0, 0, (W, H))
        det.update_plate([])
        valid += int(fg.valid)
    ratio = det.light_ratio(f, 0, 0, (W, H))
    print(f"{label}: valid frames {valid}/100, last reason '{fg.reason}', fg_ratio {fg.fg_ratio:.2f}, "
          f"stale_frames {det.stale_frames}, light_ratio {ratio:.2f} -> light_changed alert: {light_out_of_band(ratio)}, "
          f"veto support() on an empty spot = {fg.support(700, 300, 150):.2f}")
    det = ForegroundDetector(plate)

# the pipeline's plate_status / slot states through FrameProcessor (no model, no GPU work)
from core.pipeline import FrameProcessor, ProcessingSettings, ScaledTrack
s = ProcessingSettings(confidence=0.3, imgsz=640, use_fp16=False, enhance_enabled=False, enhance_lite=False,
                       enhance_force=False, person_height_px=200, use_gpu_path=False)
p = FrameProcessor(model=None, settings=s)
def st(tid, x, y):
    kp = np.tile([x, y], (17, 1)).astype(np.float64)
    return ScaledTrack(track_id=tid, keypoints=kp, confidence=np.full(17, 0.9), bbox=np.array([x - 40, y - 100, 80, 200.0]),
                       history=[], velocity=np.zeros(2), smoothed_centroid=np.array([x, y], float), frames_since_skeleton=0,
                       centroid_raw=np.array([x, y], float), hits=50, age=50, time_since_update=0, feed_src="yolo")
for i in range(10):
    p._run_identity_slots([st(7, 500 + i, 400)], lambda t: t, 1920, 1080)
    time.sleep(0.01)
print("slot_states() with the live clock (perf_counter), one dancer, max_dancers 2:", p.slot_states())
print("time.perf_counter() now:", round(time.perf_counter(), 3))
