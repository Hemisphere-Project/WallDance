"""Clean-plate foreground under dusk light drift (audit repro, CPU only, synthetic images).

Drives the real core.foreground.CleanPlate / ForegroundDetector (shipped FgParams) with the scene of
scene.py.  Sections:
  A1  pure global gain, plate as captured (no update): tolerance of the gain normalisation
  A2  pure global gain ramp 1.0 -> 0.3 over 20 min, shipped selective update, a moving dancer protected
  B1  physical dusk (IR constant, daylight fading, not proportional), plate as captured (a step: restart
      mid-show, or a take replayed on another light's plate)
  B2  physical dusk as a slow ramp (20 / 60 min) and as fast ramps, shipped selective update
  B3  the stale latch: a step the plate cannot follow, remote-ops code vs the release code
"""
import importlib.util
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scene import Scene, PH, W, H, body_mask  # noqa: E402

from core.foreground import CleanPlate, ForegroundDetector, FgParams  # noqa: E402

FPS = 20.0
P = FgParams()
SUPPORT_VETO = 0.10      # SlotParams.fg_veto / fg_support_min
SUPPORT_ENTRY = 0.30     # SlotParams.entry_fg_support


def plate_of(sc, n=40, **kw):
    return CleanPlate.from_frames([sc.frame(**kw) for _ in range(n)], source="synthetic")


def measure(det, sc, day, gain, dancers, n=10, update=False, protect=()):
    """n frames at one light level -> medians of gain, fg_ratio, validity, support under each dancer
    and at an empty spot, largest blob that is not on a dancer."""
    g, r, v, sup, emp, ghost = [], [], [], [[] for _ in dancers], [], []
    for _ in range(n):
        fg = det.process(sc.frame(day=day, gain=gain, dancers=dancers), 0, 0, (W, H))
        if update:
            det.update_plate(protect)
        g.append(fg.gain)
        r.append(fg.fg_ratio)
        v.append(fg.valid)
        for i, d in enumerate(dancers):
            sup[i].append(fg.support(d[0], d[1], PH) if fg.valid else float("nan"))
        emp.append(fg.support(1100, 150, PH) if fg.valid else float("nan"))
        big = 0.0
        for b in fg.blobs:
            if all(abs(b.x - d[0]) > 0.5 * PH or abs(b.y - d[1]) > 0.7 * PH for d in dancers):
                big = max(big, b.area)
        ghost.append(big)
    med = lambda a: float(np.nanmedian(a)) if np.isfinite(a).any() else float("nan")
    return dict(gain=med(np.array(g)), ratio=med(np.array(r)), valid=float(np.mean(v)),
                sup=[med(np.array(s)) for s in sup], empty=med(np.array(emp)),
                ghost_px2=float(np.median(ghost)), ghost_h2=float(np.median(ghost)) / PH ** 2)


def row(tag, m):
    s = " ".join(f"{x:5.2f}" for x in m["sup"])
    return (f"{tag:<26} gain {m['gain']:5.2f}  fg_ratio {m['ratio']:6.3f}  valid {m['valid']:4.2f}  "
            f"support[{s}]  empty {m['empty']:5.2f}  biggest non-dancer blob {m['ghost_h2']:5.3f} h2")


RHOS = (1.5, 0.7, 1.15)
D3 = [(350, 280, RHOS[0]), (640, 250, RHOS[1]), (930, 300, RHOS[2])]


def section_a1():
    print("\n=== A1  pure global gain, plate as captured, no update (3 dancers: rho 1.5 / 0.7 / 1.15) ===")
    for sigma70 in (2.5, 5.0):
        sc = Scene(seed=1, day_frac=0.7, sigma70=sigma70)
        plate = plate_of(sc)
        thr = max(P.thr_floor, P.k * plate.sigma)
        print(f"-- sensor noise {sigma70} DN at 70 DN: plate sigma {plate.sigma:.2f} DN (downscaled) -> "
              f"threshold {thr:.2f} DN, frozen")
        for g in (1.0, 0.8, 0.667, 0.5, 0.4, 0.3, 0.25, 0.22, 0.20, 0.17, 0.15):
            det = ForegroundDetector(plate)
            print(row(f"gain x{g:4.2f}", measure(det, sc, 1.0, g, D3)))


def ramp(sc, plate, dur_s, kind, det_cls=ForegroundDetector, dancers_fn=None, protect_fn=None,
         end=0.3, checkpoints=10, hold_s=0.0):
    """A ramp at 20 fps with the shipped cadence: only every 10th frame is analysed (the others change
    no state but the cadence counter, so update_plate is called 10 times).  kind: 'gain' (global factor
    1 -> end) or 'day' (daylight share 1 -> end)."""
    det = det_cls(plate)
    n = int(dur_s * FPS)
    first_stale = None
    stale_frames = analysed = 0
    out = []
    marks = set(int(round(k * (n - 1) / checkpoints / 10.0)) * 10 for k in range(checkpoints + 1))
    total = n + int(hold_s * FPS)
    for i in range(9, total, 10):                      # the frame whose update_plate call blends
        u = min(1.0, i / max(1, n - 1))
        level = 1.0 + (end - 1.0) * u
        day, gain = (1.0, level) if kind == "gain" else (level, 1.0)
        t = i / FPS
        dancers = dancers_fn(t) if dancers_fn else ()
        protect = protect_fn(t, dancers) if protect_fn else ()
        fg = det.process(sc.frame(day=day, gain=gain, dancers=dancers), 0, 0, (W, H))
        det._frames += 9                               # the 9 frames in between (no blend on them)
        det.update_plate(protect)
        analysed += 1
        if not fg.valid:
            stale_frames += 1
            if first_stale is None:
                first_stale = (t, level, sc.light_ratio_true(day, gain))
        if (i - 9) in marks or i + 10 >= total:
            sup = [fg.support(d[0], d[1], PH) if fg.valid else float("nan") for d in dancers]
            big = 0.0
            for b in fg.blobs:
                if all(abs(b.x - d[0]) > 0.5 * PH or abs(b.y - d[1]) > 0.7 * PH for d in dancers):
                    big = max(big, b.area)
            out.append((t, level, sc.light_ratio_true(day, gain), fg.gain, fg.fg_ratio, fg.valid, sup,
                        big / PH ** 2, det.light_ratio(sc.frame(day=day, gain=gain), 0, 0, (W, H))))
    return det, out, first_stale, stale_frames, analysed


def print_ramp(out, first_stale, stale_frames, analysed):
    for t, level, true, gain, ratio, valid, sup, big, lr in out:
        s = " ".join(f"{x:5.2f}" for x in sup)
        print(f"  t {t:7.1f} s  level {level:4.2f}  true light {true:4.2f}x  alert ratio {lr:4.2f}"
              f"{' ALERT' if not (1/1.5 <= lr <= 1.5) else '      '}  plate gain {gain:4.2f}  "
              f"fg_ratio {ratio:6.3f}  {'valid' if valid else 'STALE'}  support[{s}]  ghost {big:5.3f} h2")
    fs = "never" if first_stale is None else f"t {first_stale[0]:.1f} s (true light {first_stale[2]:.2f}x)"
    print(f"  first stale: {fs}; stale analysed frames {stale_frames}/{analysed}")


def moving(t):
    """Three dancers: one walks, one sways, one hangs still 15 s out of 30."""
    a = (350 + 150 * np.sin(2 * np.pi * t / 40.0), 280 + 60 * np.sin(2 * np.pi * t / 23.0), RHOS[0])
    b = (640 + 30 * np.sin(2 * np.pi * t / 9.0), 250, RHOS[1])
    ph = t % 30.0
    c = (930 + (0 if ph < 15 else 120 * np.sin(2 * np.pi * (ph - 15) / 15.0)), 300, RHOS[2])
    return [a, b, c]


def protect_all(t, dancers):
    """What the pipeline protects when YOLO sees every dancer: a box 0.6 h x 1.2 h around each."""
    return [(d[0] - 0.3 * PH, d[1] - 0.6 * PH, d[0] + 0.3 * PH, d[1] + 0.6 * PH) for d in dancers]


def section_a2():
    print("\n=== A2  pure global gain ramp 1.0 -> 0.3 over 20 min, shipped update, 3 dancers protected ===")
    sc = Scene(seed=2, day_frac=0.7)
    det, out, fs, sf, an = ramp(sc, plate_of(sc), 1200.0, "gain", dancers_fn=moving, protect_fn=protect_all)
    print_ramp(out, fs, sf, an)


def section_b1():
    print("\n=== B1  physical dusk as a STEP (plate as captured, no update): daylight share left ===")
    for day_frac in (0.3, 0.5, 0.7, 0.8):
        sc = Scene(seed=3, day_frac=day_frac)
        plate = plate_of(sc)
        print(f"-- daylight = {day_frac:.0%} of the wall light at Calibrate (full night = "
              f"{sc.light_ratio_true(0.0):.2f}x)")
        for day in (1.0, 0.9, 0.8, 0.6, 0.4, 0.2, 0.0):
            det = ForegroundDetector(plate)
            m = measure(det, sc, day, 1.0, D3)
            print(row(f"daylight left {day:3.1f} ({sc.light_ratio_true(day):4.2f}x)", m))


def section_b2():
    print("\n=== B2  physical dusk as a RAMP, shipped selective update, 3 dancers protected ===")
    for dur, label in ((3600.0, "60 min"), (1200.0, "20 min"), (300.0, "5 min"), (120.0, "2 min"),
                       (30.0, "30 s"), (5.0, "5 s")):
        sc = Scene(seed=4, day_frac=0.7)
        print(f"-- daylight 70 % of the light at Calibrate, fading to 0 in {label}, then 60 s of night")
        det, out, fs, sf, an = ramp(sc, plate_of(sc), dur, "day", dancers_fn=moving,
                                    protect_fn=protect_all, end=0.0,
                                    checkpoints=6 if dur >= 300 else 3, hold_s=60.0)
        print_ramp(out, fs, sf, an)


def section_b3():
    print("\n=== B3  the stale latch after a change the plate cannot follow (daylight share 0.7 -> 0 in 5 s,"
          " then 5 min of night) ===")
    spec = importlib.util.spec_from_file_location("foreground_release", HERE / "foreground_release.py")
    rel = importlib.util.module_from_spec(spec)
    sys.modules["foreground_release"] = rel
    spec.loader.exec_module(rel)
    for name, cls in (("remote-ops (0e125cc, unreleased)", ForegroundDetector),
                      ("release 63f4989 (the laptop)", rel.ForegroundDetector)):
        sc = Scene(seed=5, day_frac=0.7)
        det, out, fs, sf, an = ramp(sc, plate_of(sc), 5.0, "day", det_cls=cls, dancers_fn=moving,
                                    protect_fn=protect_all, end=0.0, checkpoints=1, hold_s=300.0)
        print(f"-- {name}")
        print_ramp(out[-3:], fs, sf, an)
    print("-- restart at night on the dusk snapshot file (live plate lost): = B1 'daylight left 0.0' rows")


def section_b4():
    print("\n=== B4  a NIGHT plate (Calibrate the evening before, IR only) used from dusk: 20 min ramp ===")
    spec = importlib.util.spec_from_file_location("foreground_release", HERE / "foreground_release.py")
    rel = importlib.util.module_from_spec(spec)
    sys.modules["foreground_release"] = rel
    spec.loader.exec_module(rel)
    for name, cls in (("remote-ops (0e125cc, unreleased)", ForegroundDetector),
                      ("release 63f4989 (the laptop)", rel.ForegroundDetector)):
        sc = Scene(seed=6, day_frac=0.7)
        night = CleanPlate.from_frames([sc.frame(day=0.0) for _ in range(40)], source="synthetic night")
        det, out, fs, sf, an = ramp(sc, night, 1200.0, "day", det_cls=cls, dancers_fn=moving,
                                    protect_fn=protect_all, end=0.0, checkpoints=8, hold_s=60.0)
        print(f"-- {name}: plate sigma {night.sigma:.2f} DN")
        print_ramp(out, fs, sf, an)


def section_c1():
    print("\n=== C1  a dancer YOLO has lost (not protected) hanging still: absorbed by the selective update ===")
    print("    (the pipeline protects LIVE slots, backed belts and tracks with a skeleton <= 20 frames old;")
    print("     a slot held by the foreground alone is not in that list)")
    for light, day in ((1.0, 1.0), (0.31, 0.0)):
        for rho in (1.5, 0.7, 1.15):
            sc = Scene(seed=8, day_frac=0.7)
            det = ForegroundDetector(plate_of(sc))
            if day < 1.0:                       # the plate has followed the dusk (B2): the night wall, in the
                from core.foreground import _down   # snapshot's brightness scale; sigma stays the dusk one
                det._live = _down(sc.clean(day=day)) / sc.light_ratio_true(day)
                for _ in range(40):
                    det.process(sc.frame(day=day), 0, 0, (W, H))
                    det._frames += 9
                    det.update_plate(())
            d = (640.0, 280.0, rho)
            gone = None
            trace = []
            for i in range(9, int(90 * FPS), 10):
                fg = det.process(sc.frame(day=day, dancers=[d]), 0, 0, (W, H))
                det._frames += 9
                det.update_plate(())
                s = fg.support(d[0], d[1], PH) if fg.valid else float("nan")
                body = max([b.area for b in fg.blobs if abs(b.x - d[0]) < 0.5 * PH and abs(b.y - d[1]) < 0.7 * PH]
                           or [0.0]) / PH ** 2
                trace.append((i / FPS, s, body))
                if gone is None and (s < SUPPORT_VETO or body < 0.02):
                    gone = i / FPS
            # the dancer leaves: what stays at the spot
            imprint = []
            for i in range(9, int(60 * FPS), 10):
                fg = det.process(sc.frame(day=day), 0, 0, (W, H))
                det._frames += 9
                det.update_plate(())
                body = max([b.area for b in fg.blobs if abs(b.x - d[0]) < 0.5 * PH and abs(b.y - d[1]) < 0.7 * PH]
                           or [0.0]) / PH ** 2
                imprint.append((i / FPS, fg.support(d[0], d[1], PH), body))
            last = next((t for t, s, b in imprint if b < 0.02), None)
            pick = lambda tr, tt: min(tr, key=lambda r: abs(r[0] - tt))
            print(f"light {light:4.2f}x rho {rho:4.2f}: support at 0.5 s {trace[0][1]:.2f}, 10 s {pick(trace, 10)[1]:.2f}, "
                  f"20 s {pick(trace, 20)[1]:.2f}, 30 s {pick(trace, 30)[1]:.2f}, 60 s {pick(trace, 60)[1]:.2f}; "
                  f"body evidence gone (support < {SUPPORT_VETO} or blob < 0.02 h2) at "
                  f"{'never in 90 s' if gone is None else f'{gone:.1f} s'}; after the dancer leaves: imprint blob "
                  f"{imprint[0][2]:.3f} h2 (support {imprint[0][1]:.2f}), gone after "
                  f"{'> 60 s' if last is None else f'{last:.1f} s'}")


if __name__ == "__main__":
    t0 = time.time()
    which = sys.argv[1:] or ["a1", "a2", "b1", "b2", "b3"]
    for w in which:
        globals()[f"section_{w}"]()
    print(f"\n[{time.time() - t0:.0f} s]")
