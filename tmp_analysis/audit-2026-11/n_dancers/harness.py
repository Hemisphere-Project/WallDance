"""Throwaway harness (audit 2026-11-18, regime n-greater-than-2).

Drives the SHIPPED post-YOLO chain, CPU only, no model, no footage:
  FrameProcessor._guard_person_height -> _filter_duplicate_detections (size gate, small-splice,
  shadow suppression, dedupe) -> replay_gpu_cached (= _feed_motion_detectors + _post_yolo_chain:
  cross-validation, exclusion, cold blobs, DancerTracker.update, finalize, IdentitySlots.update,
  OSC send) with synthetic YOLO detections and a synthetic gray frame.

Defaults: config.DETECTION_DEFAULTS / ProcessingSettings defaults (max_dancers 2, static guard ON,
smart hold ON, belt ON, no plate -> foreground rules off), tracker defaults (max_persons 6).
Tracker space == original space (frame 1280x1280, imgsz 1280, scale 1, pad 0).
"""
import os
import socket
import sys
import tempfile
import time

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core import config  # noqa: E402
from core.osc_output import OSCSender  # noqa: E402
from core.ops_monitor import HealthMonitor  # noqa: E402
from core.pipeline import FrameProcessor, ProcessingSettings  # noqa: E402
from core.tracker import DancerTrack  # noqa: E402

W = H = 1280
FPS = 20.0
PH = 125                      # person height, px (the dancer at the 25 m wall is ~123-128 px at 1280)

# COCO-17 standing skeleton, normalised by body height, zero-mean (centroid == body centre)
_T = np.array([
    (0.00, -0.42), (0.02, -0.44), (-0.02, -0.44), (0.04, -0.43), (-0.04, -0.43),
    (0.10, -0.30), (-0.10, -0.30), (0.14, -0.14), (-0.14, -0.14), (0.15, 0.00), (-0.15, 0.00),
    (0.07, 0.02), (-0.07, 0.02), (0.07, 0.24), (-0.07, 0.24), (0.07, 0.46), (-0.07, 0.46)], float)
TEMPLATE = _T - _T.mean(axis=0)


def make_det(x, y, h, rng, conf=0.8, n_visible=17, jitter=0.01):
    """(keypoints, confidence, bbox xywh) like FrameProcessor._extract_detections."""
    k = TEMPLATE * h + np.array([x, y]) + rng.normal(0, jitter * h, (17, 2))
    c = np.clip(rng.normal(conf, 0.04, 17), 0.05, 0.99)
    if n_visible < 17:                       # legs / arms go first, like a dark or cut body
        hide = [16, 15, 14, 13, 10, 9, 8, 7, 4, 3, 2, 1][: 17 - n_visible]
        c[hide] = 0.05
    bbox = np.array([x - 0.2 * h, y - 0.5 * h, 0.4 * h, h], dtype=np.float64)
    return k, c, bbox


class Scene:
    """Synthetic gray: dark noisy wall + bright body ellipses (and optional static figures
    that are part of the wall from frame 0)."""

    def __init__(self, seed=0, static_figures=()):
        rng = np.random.default_rng(seed)
        base = np.full((H, W), 25.0, np.float32)
        for (x, y, h) in static_figures:     # a wall stain shaped like a person
            cv2.ellipse(base, (int(x), int(y)), (int(0.2 * h), int(0.5 * h)), 0, 0, 360, 60.0, -1)
        self.base = base
        self.noise = [rng.normal(0, 2.0, (H, W)).astype(np.float32) for _ in range(8)]
        self.i = 0

    def render(self, bodies):
        img = self.base + self.noise[self.i % len(self.noise)]
        self.i += 1
        for (x, y, h) in bodies:
            cv2.ellipse(img, (int(x), int(y)), (int(0.2 * h), int(0.5 * h)), 0, 0, 360, 95.0, -1)
        return np.clip(img, 0, 255).astype(np.uint8)


def make_proc(max_dancers=None, max_persons=None, person_height=PH, osc=True, log=True, **settings_kw):
    DancerTrack._id_counter = 0
    s = ProcessingSettings(confidence=config.DETECTION_DEFAULTS["confidence"],
                           imgsz=config.DETECTION_DEFAULTS["yolo_imgsz"], use_fp16=False,
                           enhance_enabled=False, enhance_lite=False, enhance_force=False,
                           person_height_px=person_height, use_gpu_path=False)
    for k, v in settings_kw.items():
        setattr(s, k, v)
    p = FrameProcessor(model=None, settings=s)
    if max_dancers is not None:
        p.configure_identity_slots(max_dancers=max_dancers)
    if max_persons is not None:
        p.tracker.max_persons = max_persons
    p._t = 0.0
    p.set_output_clock(lambda: p._t)
    if log:
        p.tracker.logger.start_session(tempfile.mkdtemp(prefix="wdlog_", dir=os.path.dirname(__file__)))
    else:
        p.tracker.logger.enabled = False
    p._n_dgram = 0
    if osc:
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        p._rx = rx
        sender = OSCSender("127.0.0.1", rx.getsockname()[1])
        sender.enabled = True
        orig = sender._send

        def counted(addr, args, _o=orig):
            p._n_dgram += 1
            return _o(addr, args)
        sender._send = counted
        p.attach_osc(sender)
    # time the slot layer and the whole post-YOLO chain
    p._t_slots = 0.0
    p._t_chain = 0.0
    orig_slots = p._run_identity_slots

    def timed_slots(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig_slots(*a, **k)
        finally:
            p._t_slots = (time.perf_counter() - t0) * 1000
    p._run_identity_slots = timed_slots
    orig_chain = p._post_yolo_chain

    def timed_chain(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig_chain(*a, **k)
        finally:
            p._t_chain = (time.perf_counter() - t0) * 1000
    p._post_yolo_chain = timed_chain
    return p


def step(proc, dets, gray, frame_no, box_conf=0.6):
    """One frame through the shipped chain.  Returns (reported tracker tracks, emitted slot
    tracks, timing dict)."""
    proc._t = frame_no / FPS
    proc._last_box_confs = {proc._bbox_conf_key(d[2]): box_conf for d in dets}
    proc.last_raw_det_heights = [float(d[2][3]) for d in dets]
    ph = int(proc.settings.person_height_px)
    t0 = time.perf_counter()
    proc._guard_person_height(dets, 1.0)
    n_in = len(dets)
    dets = proc._filter_duplicate_detections(dets, effective_person_height=ph)
    t_pre = (time.perf_counter() - t0) * 1000
    timing = {}
    space = dict(person_height=ph, scale=1.0, pad_x=0.0, pad_y=0.0, roi_x=0, roi_y=0, frame_width=W)
    n0 = proc._n_dgram
    t0 = time.perf_counter()
    tracks = proc.replay_gpu_cached(dets, space, gray, W, H, frame_no, timing)
    t_all = (time.perf_counter() - t0) * 1000
    timing["pre_ms"] = t_pre
    timing["n_det_in"] = n_in
    timing["n_det_kept"] = len(dets)
    timing["slots_ms"] = proc._t_slots
    timing["chain_ms"] = proc._t_chain
    timing["motion_feed_ms"] = t_all - proc._t_chain
    timing["dgrams"] = proc._n_dgram - n0
    return tracks, list(proc.last_emitted), timing


class Health:
    """The real HealthMonitor ticked at 1 Hz the way runtime/main_loop.py feeds it."""

    def __init__(self):
        self.h = HealthMonitor()
        self.alerts = []
        self._last = -1

    def tick(self, proc, tracks, frame_no):
        sec = int(frame_no / FPS)
        if sec == self._last:
            return
        self._last = sec
        for a in self.h.tick(float(sec), fps=FPS, n_tracked=len(tracks), in_run=True, model_ready=True,
                             camera_open=True, camera_reconnecting=False, playback_active=False,
                             n_over_cap=proc.tracker.last_over_cap):
            self.alerts.append((sec, a.kind, a.message))


def score(frames_gt, frames_out, on_h=0.5):
    """frames_gt[i] = {name: (x, y, h)} present dancers; frames_out[i] = [(slot id, x, y, state)].
    A point is ON a dancer when within on_h x h of its centre (nearest dancer wins)."""
    names = sorted({n for g in frames_gt for n in g})
    present = {n: 0 for n in names}
    covered = {n: 0 for n in names}
    cur_hole = {n: 0 for n in names}
    max_hole = {n: 0 for n in names}
    ids_seen = {n: [] for n in names}       # sequence of slot ids on the dancer (changes only)
    ghost_frames = 0
    ghost_points = 0
    n_points = {}
    slot_owner = {}
    slot_moves = 0                           # a slot id seen on a different dancer than before
    first_point = {n: None for n in names}
    first_present = {n: None for n in names}
    for i, (g, o) in enumerate(zip(frames_gt, frames_out)):
        n_points[len(o)] = n_points.get(len(o), 0) + 1
        hit = {}
        ghosts = 0
        for sid, x, y, st in o:
            best, bd = None, 1e9
            for n, (gx, gy, gh) in g.items():
                d = float(np.hypot(x - gx, y - gy)) / gh
                if d < bd:
                    best, bd = n, d
            if best is not None and bd <= on_h:
                hit.setdefault(best, []).append(sid)
                if slot_owner.get(sid) not in (None, best):
                    slot_moves += 1
                slot_owner[sid] = best
            else:
                ghosts += 1
        ghost_points += ghosts
        ghost_frames += 1 if ghosts else 0
        for n in g:
            present[n] += 1
            if first_present[n] is None:
                first_present[n] = i
            if n in hit:
                covered[n] += 1
                cur_hole[n] = 0
                if first_point[n] is None:
                    first_point[n] = i
                sid = hit[n][0]
                if not ids_seen[n] or ids_seen[n][-1] != sid:
                    ids_seen[n].append(sid)
            else:
                cur_hole[n] += 1
                max_hole[n] = max(max_hole[n], cur_hole[n])
        for n in names:
            if n not in g:
                cur_hole[n] = 0
    out = {"frames": len(frames_gt), "points_per_frame": dict(sorted(n_points.items())),
           "ghost_frames": ghost_frames, "ghost_points": ghost_points, "slot_moves": slot_moves,
           "per_dancer": {}}
    for n in names:
        out["per_dancer"][n] = {
            "present_s": round(present[n] / FPS, 2),
            "coverage": round(covered[n] / max(1, present[n]), 3),
            "longest_hole_s": round(max_hole[n] / FPS, 2),
            "first_point_after_s": (None if first_point[n] is None
                                    else round((first_point[n] - first_present[n]) / FPS, 2)),
            "slot_ids": ids_seen[n]}
    return out


def emitted_rows(emitted):
    return [(int(t.track_id), float(t.smoothed_centroid[0]), float(t.smoothed_centroid[1]),
             t.slot_state) for t in emitted]
