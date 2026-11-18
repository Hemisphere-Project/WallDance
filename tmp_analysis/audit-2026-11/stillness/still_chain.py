"""Throwaway audit script (long-stillness regime).  CPU only, no model, no footage.

Drives the REAL post-YOLO chain (FrameProcessor.replay_gpu_cached -> _post_yolo_chain:
MOG2 + frame-diff feed, motion cross-validation, exclusion, cold blobs, DancerTracker.update,
IdentitySlots.update, clean-plate foreground + selective plate update) with SYNTHETIC grayscale
frames and SYNTHETIC YOLO detections at 20 fps.  Shipped defaults (config.DETECTION_DEFAULTS,
ProcessingSettings / SlotParams defaults as the pipeline builds them) unless a scenario says so.

It proves logic paths and time constants, NOT field accuracy.
"""
from __future__ import annotations

import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")     # never touch the GPU
APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import core.config as config  # noqa: E402
from core.foreground import CleanPlate  # noqa: E402
from core.pipeline import FrameProcessor, ProcessingSettings  # noqa: E402

FPS = 20.0
W, H = 1280, 720
IMGSZ = 1280
PAD_Y = (IMGSZ - H) // 2          # letterbox of a 1280x720 ROI into 1280x1280: scale 1, pad_y 280
PH = 127                          # the night dancer's height (px), docs/MULTIPASS_REVIEW §3.7
SPACE = {"person_height": PH, "scale": 1.0, "pad_x": 0.0, "pad_y": float(PAD_Y),
         "roi_x": 0, "roi_y": 0, "frame_width": IMGSZ}

_KP = np.array([  # COCO-17 offsets in units of the body height, relative to the box centre
    (0.00, -0.42), (0.02, -0.44), (-0.02, -0.44), (0.04, -0.43), (-0.04, -0.43),
    (0.11, -0.30), (-0.11, -0.30), (0.13, -0.12), (-0.13, -0.12), (0.14, 0.03), (-0.14, 0.03),
    (0.07, 0.02), (-0.07, 0.02), (0.07, 0.25), (-0.07, 0.25), (0.07, 0.46), (-0.07, 0.46)])


def detection(cx, cy, rng, kconf=0.6, n_valid=17, h=PH, jitter=1.0):
    """One synthetic YOLO pose detection in TRACKER (letterbox) space."""
    sh = rng.normal(0, jitter, 2)                       # whole-box jitter
    kp = np.array([cx, cy + PAD_Y]) + _KP * h + sh + rng.normal(0, jitter, (17, 2))
    conf = np.full(17, 0.05)
    conf[:n_valid] = kconf
    bbox = np.array([cx - 0.18 * h + sh[0], cy + PAD_Y - 0.5 * h + sh[1], 0.36 * h, h], dtype=np.float64)
    return kp.astype(np.float64), conf.astype(np.float64), bbox


class Scene:
    """Synthetic raw mono frames: a textured dark wall + sensor noise + bodies (soft ellipses)."""

    def __init__(self, wall=30.0, tex=4.0, noise=2.0, seed=1):
        self.rng = np.random.default_rng(seed)
        t = self.rng.normal(0, 1, (H // 8, W // 8)).astype(np.float32)
        t = cv2.resize(cv2.GaussianBlur(t, (0, 0), 1.5), (W, H), interpolation=cv2.INTER_CUBIC)
        self.wall = (wall + tex * t / max(1e-6, float(t.std()))).astype(np.float32)
        self.noise = noise

    def frame(self, bodies=()):
        img = self.wall.copy()
        for cx, cy, contrast, h in bodies:
            m = np.zeros((H, W), np.float32)
            cv2.ellipse(m, (int(round(cx)), int(round(cy + 0.06 * h))), (int(0.16 * h), int(0.44 * h)), 0, 0, 360, 1.0, -1)
            cv2.circle(m, (int(round(cx)), int(round(cy - 0.42 * h))), int(0.07 * h), 1.0, -1)
            img += contrast * cv2.GaussianBlur(m, (0, 0), 1.5)
        img += self.rng.normal(0, self.noise, img.shape).astype(np.float32)
        return np.clip(img + 0.5, 0, 255).astype(np.uint8)


def make_proc(max_dancers=None, plate=None, belt=False):
    s = ProcessingSettings(
        confidence=config.DETECTION_DEFAULTS["confidence"], imgsz=IMGSZ, use_fp16=False,
        enhance_enabled=True, enhance_lite=False, enhance_force=False,
        person_height_px=PH, use_gpu_path=False, osc_enabled=False)
    if max_dancers is not None:
        s.max_dancers = int(max_dancers)
    s.use_ir_belt = bool(belt)      # no belt worn in the synthetic frames; s2c (real) covers the belt
    proc = FrameProcessor(model=None, settings=s)
    proc.tracker.reset()
    proc.tracker.logger.start_session(tempfile.mkdtemp(prefix="wd_audit_"))
    clock = {"frame": 0}
    proc.set_output_clock(lambda: clock["frame"] / FPS)
    if plate is not None:
        proc.set_clean_plate(plate)
    return proc, clock


def body_box_orig(cx, cy, h=PH):
    return cx - 0.18 * h, cy - 0.5 * h, 0.36 * h, h


def step(proc, clock, i, gray, dets):
    clock["frame"] = i
    timing = {}
    proc.replay_gpu_cached(dets, SPACE, gray, W, H, i, timing)
    em = [(int(t.track_id), t.slot_state) for t in proc.last_emitted]
    trk = []
    reasons = proc.tracker.last_emit_reasons
    for t in proc.tracker.tracks:
        trk.append((t.track_id, reasons.get(t.track_id), int(t.time_since_update), int(t._frames_since_skeleton),
                    t._feed_src, round(float(t._warmup_score), 1)))
    return em, trk, timing


def motion_probe(proc, cx, cy):
    md = proc.motion_model.detector
    x, y, w, h = body_box_orig(cx, cy)
    mog = md.motion_ratio_in_bbox(x, y, w, h, include_shadows=True, use_clean_mask=False, require_coherence=False)
    _b, fd = md.frame_diff_blob_in_bbox(x, y, w, h, min_ratio=0.0)
    return mog, fd


def fg_probe(proc, cx, cy):
    fg = proc.last_fg
    if fg is None:
        return None, None
    if not fg.valid:
        return "invalid:" + fg.reason, None
    area = max([b.area for b in fg.blobs if np.hypot(b.x - cx, b.y - cy) < 0.6 * PH] or [0.0])
    return round(fg.support(cx, cy, PH), 3), int(area)


def run_solo(name, yolo, *, plate_on, contrast=30.0, still_s=20.0, kconf=0.6, n_valid=17, noise=2.0,
             walk_frames=60, verbose=True, ret_kconf=None):
    """Empty wall 4 s -> dancer walks in (3 s) -> motionless `still_s` -> walks on (4 s).
    `yolo(k)` -> bool: does YOLO detect the dancer on still-frame k (k = frames since she stopped)?"""
    sc = Scene(noise=noise)
    rng = np.random.default_rng(7)
    empties = [sc.frame() for _ in range(80)]
    plate = CleanPlate.from_frames(empties[20:60], source="synthetic") if plate_on else None
    proc, clock = make_proc(plate=plate)
    n_still = int(still_s * FPS)
    X0, X1, Y = 100.0, 640.0, 360.0
    log = []
    i = 0
    for g in empties:
        step(proc, clock, i, g, [])
        i += 1
    for k in range(walk_frames):                      # entry: a clear, moving dancer
        x = X0 + (X1 - X0) * (k + 1) / walk_frames
        em, trk, tm = step(proc, clock, i, sc.frame([(x, Y, contrast, PH)]), [detection(x, Y, rng)])
        log.append(("walk", k, em, trk, tm, None, None))
        i += 1
    for k in range(n_still):                           # the stillness
        dets = [detection(X1, Y, rng, kconf=kconf, n_valid=n_valid)] if yolo(k) else []
        em, trk, tm = step(proc, clock, i, sc.frame([(X1, Y, contrast, PH)]), dets)
        log.append(("still", k, em, trk, tm, motion_probe(proc, X1, Y), fg_probe(proc, X1, Y)))
        i += 1
    for k in range(80):                                # she moves again (YOLO back after 5 frames)
        x = X1 + 6.0 * (k + 1)
        dets = [detection(x, Y, rng, kconf=(ret_kconf or 0.6))] if k >= 5 else []
        em, trk, tm = step(proc, clock, i, sc.frame([(x, Y, contrast, PH)]), dets)
        log.append(("move", k, em, trk, tm, None, None))
        i += 1
    proc.tracker.logger.close()
    if verbose:
        report(name, log, n_still, proc)
    return log, proc


def report(name, log, n_still, proc):
    print("=" * 110)
    print(name)
    still = [r for r in log if r[0] == "still"]
    move = [r for r in log if r[0] == "move"]
    states = Counter((r[2][0][1] if r[2] else "NONE") for r in still)
    print("  stillness %.0f s (%d frames): emitted states %s" % (n_still / FPS, n_still, dict(states)))
    # state-change events during stillness + after
    prev = None
    ev = []
    for ph, k, em, trk, tm, mp, fp in still + move:
        st = ",".join("%d:%s" % e for e in em) or "NONE"
        tr = ",".join("T%d:%s" % (t[0], t[1]) for t in trk) or "-"
        key = (st, tr)
        if key != prev:
            ev.append("%s+%.2fs slot[%s] trk[%s]" % (ph, k / FPS, st, ",".join(
                "T%d:%s tsu%d fss%d %s wu%.0f" % t for t in trk) or "-"))
            prev = key
    for e in ev[:40]:
        print("   ", e)
    if len(ev) > 40:
        print("    ... %d more transitions" % (len(ev) - 40))
    # holes in the emitted stream during stillness
    holes, cur, start = [], 0, None
    for ph, k, em, *_ in still:
        if not em:
            if cur == 0:
                start = k
            cur += 1
        elif cur:
            holes.append((start / FPS, cur / FPS)); cur = 0
    if cur:
        holes.append((start / FPS, cur / FPS))
    print("  holes while still (start s, length s):", [(round(a, 2), round(b, 2)) for a, b in holes] or "none")
    back = next((k for ph, k, em, *_ in move if em), None)
    print("  after she moves again: point present at move+%.2f s" % (back / FPS) if back is not None
          else "  after she moves again: NO point within 4 s")
    ids = sorted({e[0] for r in still + move for e in r[2]})
    print("  slot ids used:", ids)
    xv = Counter()
    for r in still:
        for k_ in ("crossval_kept_track", "crossval_kept_motion", "crossval_kept_skeleton", "crossval_rejected"):
            xv[k_] += int(r[4].get(k_, 0))
    print("  cross-validation while still:", dict(xv))
    chk = [0, 20, 60, 100, 110, 120, 140, 200, 300, 399, 599, 799]
    row = []
    for c in chk:
        if c < len(still) and still[c][5] is not None:
            mog, fd = still[c][5]
            row.append("%.1fs mog %.3f fd %.4f" % (c / FPS, mog, fd))
    print("  motion in the body box (MOG2 share incl. shadows, frame-diff share >15 DN):", " | ".join(row))
    row = []
    for c in chk:
        if c < len(still) and still[c][6] is not None and still[c][6][0] is not None:
            row.append("%.1fs sup %s area %s" % (c / FPS, still[c][6][0], still[c][6][1]))
    if row:
        print("  clean plate at the body (support share, blob px^2):", " | ".join(row))
    if proc._slots is not None:
        print("  slot counters:", {k: v for k, v in proc._slots.counters.items() if v})


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "abc"
    print("defaults: conf %.2f, intermittent %s, max_dancers %d, coast_s %.1f, smart_hold %s, static guard %s, fg_enabled %s, "
          "gamma(default enhancer) see below" % (config.DETECTION_DEFAULTS["confidence"],
                                                  config.TRACK_WARMUP_INTERMITTENT_ENABLED,
                                                  config.IDENTITY_SLOTS_MAX_DANCERS, config.IDENTITY_SLOTS_COAST_S,
                                                  config.IDENTITY_SLOTS_SMART_HOLD, config.IDENTITY_SLOTS_STATIC_GUARD,
                                                  config.FOREGROUND_ENABLED))
    p0, _ = make_proc()
    print("enhancer gamma %.2f (motion gray LUT), MOG2 var %.0f scale %.2f lr %.4f" % (
        p0.enhancer.gamma, p0.motion_model.detector._var_threshold, p0.motion_model.detector._scale,
        p0.motion_model.detector._learn_rate))
    always = lambda k: True
    drop3 = lambda k: not (100 <= k < 160 or 220 <= k < 280 or 340 <= k < 400)   # off at 5-8, 11-14, 17-20 s
    after5 = lambda k: k < 100
    if "a" in which:
        for plate in (False, True):
            run_solo("(a1) steady YOLO, clear skeleton (17 kpts @0.60) | plate %s" % plate, always, plate_on=plate)
        run_solo("(a2) steady YOLO, dim skeleton (10 kpts @0.35: not 'strong') | no plate", always, plate_on=False,
                 kconf=0.35, n_valid=10)
        run_solo("(a3) steady YOLO box, NO keypoint above 0.30 (kpts @0.25) | no plate", always, plate_on=False,
                 kconf=0.25, n_valid=17)
        run_solo("(a3) steady YOLO box, NO keypoint above 0.30 (kpts @0.25) | plate", always, plate_on=True,
                 kconf=0.25, n_valid=17)
    if "b" in which:
        for plate in (False, True):
            run_solo("(b1) YOLO off 5-8, 11-14, 17-20 s; clear skeleton when on | plate %s" % plate, drop3, plate_on=plate)
            run_solo("(b2) YOLO off 5-8, 11-14, 17-20 s; DIM skeleton when on (10 kpts @0.35) | plate %s" % plate,
                     drop3, plate_on=plate, kconf=0.35, n_valid=10)
    if "c" in which:
        run_solo("(c1) no YOLO after 5 s of stillness | NO plate", after5, plate_on=False)
        run_solo("(c2) no YOLO after 5 s | plate, body contrast 30 DN", after5, plate_on=True)
        run_solo("(c3) no YOLO after 5 s | plate, body contrast 30 DN, 40 s still", after5, plate_on=True, still_s=40.0)
        run_solo("(c4) no YOLO after 5 s | plate, FAINT body contrast 5 DN, 40 s still", after5, plate_on=True,
                 contrast=5.0, still_s=40.0)
        run_solo("(c5) no YOLO from the first still frame (just arrived: MOG2 still has her) | NO plate",
                 lambda k: False, plate_on=False)
