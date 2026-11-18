"""Ghost veto on a REAL dancer as the wall darkens (audit repro, CPU only, synthetic images).

A dancer YOLO tracks perfectly (a reported track, skeleton every frame, hits 50) walks along the wall.
The slot layer is the real one, through FrameProcessor._run_identity_slots with the real clean-plate
foreground (shipped FgParams / SlotParams).  The plate has FOLLOWED the dusk (valid at every level).
The dancer's contrast is set relative to the wall: body = wall x (1 + rel), so it is `rel x 70` DN at
Calibrate light and falls with the light.  Question: does the slot bind, or does fg_veto (support < 0.10
in the 0.45 h x h box) block a real dancer?
"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, "/data/WallDance/application/tests")
from scene import Scene, PH, W, H, body_mask  # noqa: E402

from core.foreground import CleanPlate, ForegroundDetector, _down  # noqa: E402
from core.pipeline import FrameProcessor, ProcessingSettings, ScaledTrack  # noqa: E402

FPS = 20.0


def frame(sc, day, cx, cy, rel):
    s = sc.clean(day=day)
    m = body_mask(cx, cy)
    s = np.where(m, s * (1.0 + rel), s)
    sig = np.sqrt(sc.read ** 2 + sc.c * s)
    f = s + sc.rng.standard_normal(s.shape, dtype=np.float32) * sig
    return np.clip(np.rint(f), 0, 255).astype(np.uint8)


def st(tid, x, y):
    kp = np.tile([x, y], (17, 1)).astype(np.float64)
    return ScaledTrack(track_id=tid, keypoints=kp, confidence=np.full(17, 0.9),
                       bbox=np.array([x - 0.2 * PH, y - PH / 2, 0.4 * PH, PH]), history=[],
                       velocity=np.zeros(2), smoothed_centroid=np.array([x, y], float),
                       frames_since_skeleton=0, centroid_raw=np.array([x, y], float),
                       hits=50, age=50, time_since_update=0, feed_src="yolo", box_conf=0.8)


def main():
    print("rel = body/wall - 1; contrast DN at Calibrate = |rel| x 70; plate followed the dusk (valid)")
    for sigma70 in (2.5, 5.0):
        sc = Scene(seed=11, day_frac=0.7, sigma70=sigma70)
        plate = CleanPlate.from_frames([sc.frame() for _ in range(40)], source="synthetic")
        print(f"-- sensor noise {sigma70} DN at 70 DN: threshold {max(2.0, 4 * plate.sigma):.2f} DN (frozen)")
        for rel in (0.5, -0.3, 0.2, -0.15, 0.1):
            cells = []
            for day, light in ((1.0, 1.0), (0.49, 0.65), (0.2, 0.45), (0.0, 0.31)):
                s = ProcessingSettings(confidence=0.15, imgsz=1280, use_fp16=False, enhance_enabled=False,
                                       enhance_lite=False, enhance_force=False, person_height_px=PH,
                                       use_gpu_path=False)
                s.use_ir_belt = False
                proc = FrameProcessor(model=None, settings=s)
                clock = {"t": 0.0}
                proc.set_output_clock(lambda: clock["t"])
                proc.set_clean_plate(plate)
                proc._fg_detector._live = _down(sc.clean(day=day)) / sc.light_ratio_true(day)
                first, emitted, sup, valid = None, 0, [], 0
                n = 300
                for i in range(n):
                    x = 250 + 2.5 * i                     # 50 px/s = 0.38 h/s
                    y = 280.0
                    proc._belt_gray = frame(sc, day, x, y, rel)
                    proc._belt_gray_offset = (0, 0)
                    clock["t"] += 1.0 / FPS
                    out = proc._run_identity_slots([st(7, x, y)], lambda t: t, W, H)
                    fg = proc.last_fg
                    valid += int(fg.valid)
                    sup.append(fg.support(x, y, PH) if fg.valid else np.nan)
                    if out:
                        emitted += 1
                        if first is None:
                            first = i / FPS
                c = proc._slots.counters
                cells.append(f"light {light:4.2f}x: contrast {abs(rel) * 70 * light:4.1f} DN, support median "
                             f"{np.nanmedian(sup):4.2f} (>=0.10 in {np.nanmean(np.array(sup) >= 0.10):4.0%} of frames), "
                             f"emitted {emitted / n:4.0%}, first point "
                             f"{'NEVER' if first is None else f'{first:4.2f} s'}, fg_veto {c.get('fg_veto', 0)}")
            print(f"  rel {rel:+.2f}:")
            for cell in cells:
                print("     " + cell)


if __name__ == "__main__":
    main()
