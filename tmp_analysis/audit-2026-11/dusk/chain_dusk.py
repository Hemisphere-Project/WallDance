"""Post-YOLO chain under dusk light drift (audit repro, CPU only, no model, synthetic images).

The real FrameProcessor is built the way tests/replay.py builds it (config.DETECTION_DEFAULTS filled,
load_model=False) and fed per frame, in the order of FrameProcessor._run_yolo_and_track:
  synthetic YOLO detections -> _guard_person_height -> _filter_duplicate_detections ->
  replay_gpu_cached(dets, space, raw gray, ...) = motion feed (gamma LUT, MOG2, frame-diff) -> crossval ->
  exclusion -> cold blobs -> DancerTracker.update -> finalize -> IdentitySlots.update with the real
  clean-plate foreground (ForegroundDetector) and the belt hook -> update_plate(protect) -> emit.
Not shipped defaults, on purpose: max_dancers 3 (the operator's N for a trio), person_height_px 130,
gamma 0.8, MOG2 var 16 (per-venue values a Calibrate would set).

Light: IR constant, daylight = 70 % of the wall light at Calibrate, fading to 0 in 20 min (scene.py).
Each "station" replays 90 s of show at the true drift rate, starting at a given light level; the
clean plate arrives there either FOLLOWED (pre-rolled through the dusk with the shipped selective
update) or AS CAPTURED (what a restart mid-show, or the remote-ops stale latch, leaves).

YOLO is a stand-in (the assumption of this test, not a measurement):
  perfect  every dancer every frame, box conf 0.8, keypoints 0.8
  fading   with darkness d = 0 (Calibrate light) .. 1 (night, 0.31x): moving dancer seen in
           1 - 0.3 d of the frames, still dancer in whole seconds with probability 1 - 0.75 d
           ("one second in four" on the 2026-10-06 dark still take), box conf 0.8 - 0.58 d,
           keypoints 0.8 - 0.45 d
plus a static false person on the wall (box conf 0.30, 10 keypoints at 0.5, every other frame):
the probe for the ghost veto.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "tests"))

from scene import Scene, PH, W, H  # noqa: E402

import replay  # noqa: E402  (tests/replay.py: the processor builder)
from core.foreground import CleanPlate, ForegroundDetector  # noqa: E402

FPS = 20.0
IMGSZ = 1280
SCALE = IMGSZ / float(max(W, H))          # 1.0
PAD_X = (IMGSZ - W * SCALE) / 2.0         # 0
PAD_Y = (IMGSZ - H * SCALE) / 2.0         # 360
DUSK_S = 1200.0
DAY_FRAC = 0.7
RUN_S = 90.0
GHOST = (1150.0, 430.0)


def day_for_light(L):
    return max(0.0, min(1.0, (L - (1.0 - DAY_FRAC)) / DAY_FRAC))


def choreography(t):
    """[(name, cx, cy, rho, still)] of the dancers in view at time t (s)."""
    out = [("A", 300 + 120 * np.sin(2 * np.pi * t / 20.0), 200 + 50 * np.sin(2 * np.pi * t / 13.0), 1.5, False)]
    if t < 20:
        out.append(("B", 640 + 40 * np.sin(2 * np.pi * t / 6.0), 300.0, 0.7, False))
    elif t < 45:
        out.append(("B", 640.0, 300.0, 0.7, True))                 # hangs still 25 s
    else:
        out.append(("B", 640 + 60 * np.sin(2 * np.pi * (t - 45) / 8.0), 300.0, 0.7, False))
    if t >= 30:                                                     # enters from the top
        y = min(300.0, -65.0 + 40.0 * (t - 30.0))
        if y >= 20:
            if y < 300:
                out.append(("C", 980.0, y, 1.15, False))
            elif t < 62:
                out.append(("C", 980.0, 300.0, 1.15, True))       # hangs still ~20 s
            else:
                out.append(("C", 980 + 80 * np.sin(2 * np.pi * (t - 62) / 10.0), 300.0, 1.15, False))
    return out


def skeleton(cx, cy, h, kc, rng, n_kpts=17):
    """17 COCO keypoints of a standing body in ORIGINAL px + confidences."""
    rel = np.array([[0, -0.44], [-0.02, -0.46], [0.02, -0.46], [-0.04, -0.44], [0.04, -0.44],
                    [-0.11, -0.32], [0.11, -0.32], [-0.14, -0.15], [0.14, -0.15], [-0.15, 0.0], [0.15, 0.0],
                    [-0.07, 0.03], [0.07, 0.03], [-0.07, 0.26], [0.07, 0.26], [-0.07, 0.48], [0.07, 0.48]])
    k = np.array([cx, cy]) + rel * h + rng.normal(0, 0.012 * h, (17, 2))
    c = np.clip(kc + rng.normal(0, 0.04, 17), 0.0, 1.0)
    if n_kpts < 17:
        c[n_kpts:] = 0.05
    return k, c


def to_tracker(k, bbox):
    k = k * SCALE + np.array([PAD_X, PAD_Y])
    b = np.array([bbox[0] * SCALE + PAD_X, bbox[1] * SCALE + PAD_Y, bbox[2] * SCALE, bbox[3] * SCALE],
                 dtype=np.float32)
    return k.astype(np.float32), b


def build(plate, live):
    cfg, _ = replay.fill_detection_defaults({})
    cfg.update(max_dancers=3, person_height_px=PH, gamma=0.8, mog2_var_threshold=16.0)
    proc = replay._build_processor(cfg, "yolo11x-pose", IMGSZ, load_model=False)
    clock = {"frame": 0}
    proc.set_output_clock(lambda: clock["frame"] / FPS)
    proc.set_clean_plate(plate)
    if live is not None:
        proc._fg_detector._live = live.copy()
    return proc, clock, cfg


def preroll(sc, plate, lights):
    """The live plate after following the dusk (shipped selective update, dancers protected) down to
    each light level: {L: live plate copy}."""
    det = ForegroundDetector(plate)
    stops = {L: (1.0 - day_for_light(L)) * DUSK_S for L in lights}
    out = {}
    n = int(max(stops.values()) * FPS) + 10
    pending = sorted(stops.items(), key=lambda kv: kv[1])
    for i in range(9, n, 10):
        t = i / FPS
        while pending and t >= pending[0][1]:
            out[pending.pop(0)[0]] = det._live.copy()
        day = max(0.0, 1.0 - t / DUSK_S)
        ds = choreography(t % RUN_S)
        fg = det.process(sc.frame(day=day, dancers=[(d[1], d[2], d[3]) for d in ds]), 0, 0, (W, H))
        det._frames += 9
        det.update_plate([(d[1] - 0.3 * PH, d[2] - 0.6 * PH, d[1] + 0.3 * PH, d[2] + 0.6 * PH) for d in ds])
    for L, _ in pending:
        out[L] = det._live.copy()
    return out


def run(sc, plate, live, L, yolo, seed=0, ghost=True):
    rng = np.random.default_rng(seed)
    proc, clock, cfg = build(plate, live)
    day0 = day_for_light(L)
    n = int(RUN_S * FPS)
    # MOG2 warm-up on the scene at this light (no dancers, 100 frames) -- the show has been running
    for j in range(100):
        proc._feed_motion_detectors(sc.frame(day=day0))
    still_seen = {}
    rec = []
    valid = stale = 0
    for i in range(n):
        t = i / FPS
        clock["frame"] = i
        day = max(0.0, day0 - t / DUSK_S)
        light = sc.light_ratio_true(day)
        d = min(1.0, max(0.0, (1.0 - light) / DAY_FRAC))
        ds = choreography(t)
        gray = sc.frame(day=day, dancers=[(x[1], x[2], x[3]) for x in ds])
        dets, confs = [], {}
        for name, cx, cy, rho, still in ds:
            if yolo == "perfect":
                seen, bc, kc = True, 0.8, 0.8
            else:
                bc, kc = 0.8 - 0.58 * d, 0.8 - 0.45 * d
                if still:
                    sec = (name, int(t))
                    if sec not in still_seen:
                        still_seen[sec] = rng.random() < 1.0 - 0.75 * d
                    seen = still_seen[sec]
                else:
                    seen = rng.random() < 1.0 - 0.3 * d
            if not seen:
                continue
            k, c = skeleton(cx, cy, PH, kc, rng)
            bbox = (cx - 0.2 * PH + rng.normal(0, 1.5), cy - 0.5 * PH + rng.normal(0, 1.5), 0.4 * PH, float(PH))
            kt, bt = to_tracker(k, bbox)
            dets.append((kt, c.astype(np.float32), bt))
            confs[proc._bbox_conf_key(bt)] = float(bc)
        if ghost and i % 2 == 0:
            k, c = skeleton(GHOST[0], GHOST[1], PH, 0.5, rng, n_kpts=10)
            kt, bt = to_tracker(k, (GHOST[0] - 0.2 * PH, GHOST[1] - 0.5 * PH, 0.4 * PH, float(PH)))
            dets.append((kt, c.astype(np.float32), bt))
            confs[proc._bbox_conf_key(bt)] = 0.30
        proc._last_box_confs = confs
        sph = max(1, int(proc.settings.person_height_px * SCALE))
        proc._guard_person_height(dets, 1.0 / SCALE)
        dets = proc._filter_duplicate_detections(dets, effective_person_height=sph)
        space = dict(person_height=sph, scale=SCALE, pad_x=PAD_X, pad_y=PAD_Y, roi_x=0, roi_y=0,
                     frame_width=IMGSZ)
        timing = {}
        proc.replay_gpu_cached(dets, space, gray, W, H, i, timing)
        fg = proc.last_fg
        if fg is not None:
            valid += int(fg.valid)
            stale += int(not fg.valid)
        pts = [(int(o.track_id), float(o.smoothed_centroid[0]), float(o.smoothed_centroid[1]), o.slot_state)
               for o in proc.last_emitted]
        rec.append((t, light, ds, pts, int(timing.get("crossval_rejected", 0))))
    return proc, rec, valid, stale


def score(rec):
    names = ("A", "B", "C")
    cov = {n: [] for n in names}
    ids = {n: [] for n in names}
    ghost_frames = extra = exact = 0
    ghost_at_probe = 0
    states = {}
    for t, light, ds, pts, _ in rec:
        used = set()
        for name, cx, cy, rho, still in ds:
            best, bd = None, 0.6 * PH
            for j, (sid, x, y, st) in enumerate(pts):
                dd = float(np.hypot(x - cx, y - cy))
                if dd < bd and j not in used:
                    best, bd = j, dd
            cov[name].append((t, best is not None))
            if best is not None:
                used.add(best)
                ids[name].append(pts[best][0])
                states[pts[best][3]] = states.get(pts[best][3], 0) + 1
        off = [p for j, p in enumerate(pts) if j not in used]
        if off:
            ghost_frames += 1
            if any(np.hypot(p[1] - GHOST[0], p[2] - GHOST[1]) < 0.6 * PH for p in off):
                ghost_at_probe += 1
        exact += int(len(pts) == len(ds) and not off)
    out = {}
    for n in names:
        c = cov[n]
        if not c:
            continue
        hole = cur = 0
        first = None
        for t, ok in c:
            if ok and first is None:
                first = t - c[0][0]
            cur = 0 if ok else cur + 1
            hole = max(hole, cur)
        switches = sum(1 for a, b in zip(ids[n], ids[n][1:]) if a != b)
        out[n] = dict(coverage=round(float(np.mean([ok for _, ok in c])), 3), longest_hole_s=round(hole / FPS, 2),
                      first_point_after_s=None if first is None else round(first, 2),
                      ids=sorted(set(ids[n])), id_switches=switches)
    return dict(dancers=out, exact_frames=round(exact / len(rec), 3),
                ghost_frames=ghost_frames, ghost_on_static_probe=ghost_at_probe, states=states)


if __name__ == "__main__":
    t0 = time.time()
    lights = [float(a) for a in sys.argv[1:]] or [1.0, 0.65, 0.31]
    sc = Scene(seed=7, day_frac=DAY_FRAC)
    plate = CleanPlate.from_frames([sc.frame() for _ in range(40)], source="synthetic calibrate")
    print(f"plate sigma {plate.sigma:.2f} DN -> threshold {max(2.0, 4 * plate.sigma):.2f} DN; "
          f"dancer vs wall contrast at Calibrate (median |DN| under the body): " + ", ".join(
              f"{n} {float(np.median(np.abs(sc.clean(dancers=[(x, y, r)]) - sc.clean())[__import__('scene').body_mask(x, y)])):.0f}"
              for n, x, y, r in (("A", 300, 200, 1.5), ("B", 640, 300, 0.7), ("C", 980, 300, 1.15))))
    lives = preroll(sc, plate, lights)
    print(f"[pre-roll {time.time() - t0:.0f} s]")
    results = []
    for L in lights:
        for plate_mode in ("followed", "as captured"):
            if L == 1.0 and plate_mode == "as captured":
                continue
            for yolo in ("perfect", "fading"):
                proc, rec, valid, stale = run(sc, plate, lives[L] if plate_mode == "followed" else None, L, yolo)
                s = score(rec)
                c = proc._slots.counters
                row = dict(light=L, light_end=round(rec[-1][1], 2), plate=plate_mode, yolo=yolo,
                           plate_valid=round(valid / max(1, valid + stale), 3), **s,
                           fg_veto=c.get("fg_veto", 0), fg_frames=c.get("fg_frames", 0),
                           belt_frames=c.get("belt_frames", 0), entries=c.get("entries", 0),
                           rebinds=c.get("rebinds", 0), crossval_rejected=sum(r[4] for r in rec),
                           person_height_px=int(proc.settings.person_height_px))
                results.append(row)
                print(json.dumps(row), flush=True)
    (HERE / ("out_chain_" + "_".join(str(x) for x in lights) + ".json")).write_text(json.dumps(results, indent=1))
    print(f"[{time.time() - t0:.0f} s]")
