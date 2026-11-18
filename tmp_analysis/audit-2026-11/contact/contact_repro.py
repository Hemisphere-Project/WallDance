#!/usr/bin/env python
"""Throwaway audit script (2026-11-18): the "contact-and-crossings" regime through the SHIPPED
post-YOLO chain, CPU only, no footage.

Drives, per synthetic 20 fps frame:
  FrameProcessor._guard_person_height -> _filter_duplicate_detections (pipeline.py:747-748)
  -> FrameProcessor.replay_gpu_cached (the shipped mirror of the live tail, pipeline.py:825)
     = crossval gate -> exclusion -> cold blobs -> DancerTracker.update -> IdentitySlots.update -> _emit
and reads FrameProcessor.last_emitted (what OSC /walldance/count + /dancer/centroid would carry).

Shipped defaults everywhere (ProcessingSettings defaults = config.* ; SlotParams as built by
_run_identity_slots).  Two tiers:
  logic  : gray=None  -> no motion model feed, no belt, no foreground (pure association logic)
  images : rendered gray frames (dark wall 14 DN + noise, bodies 42 DN) + an empty-wall plate
           -> MOG2 / frame-diff bridge, cold blobs, clean-plate foreground hold are live.
Nothing is written outside this script's own temp dir.
"""
import os
import sys
import tempfile
from collections import Counter

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")     # CPU only
import numpy as np
import cv2

sys.path.insert(0, "/data/WallDance/application/src")
import core.config as C                                     # noqa: E402
from core.pipeline import FrameProcessor, ProcessingSettings  # noqa: E402
from core.tracker import DancerTrack                         # noqa: E402
from core.foreground import CleanPlate                       # noqa: E402

FPS = 20.0
W, HF = 1280, 720
PH = 128                        # dancer height in tracker px (25 m wall: ~127 px, CONTEXT / config.py:375-380)
YC = 360.0
R_ON = 0.5 * PH                 # "a point for this dancer": within half a body height of its true centroid

# 17 COCO keypoints, units of body height, relative to the box centre
TPL_A = np.array([
    (0.00, -0.43), (0.02, -0.45), (-0.02, -0.45), (0.04, -0.44), (-0.04, -0.44),
    (0.11, -0.31), (-0.11, -0.31), (0.14, -0.15), (-0.14, -0.15), (0.15, 0.00), (-0.15, 0.00),
    (0.07, 0.03), (-0.07, 0.03), (0.07, 0.25), (-0.07, 0.25), (0.07, 0.46), (-0.07, 0.46)])
TPL_B = TPL_A.copy()            # B: arms out, wider stance (a different body shape for the shape cost)
TPL_B[[7, 8]] = [(0.18, -0.15), (-0.18, -0.15)]
TPL_B[[9, 10]] = [(0.22, 0.00), (-0.22, 0.00)]
TPL_B[[13, 14]] = [(0.10, 0.25), (-0.10, 0.25)]
TPL_B[[15, 16]] = [(0.14, 0.46), (-0.14, 0.46)]
TPL = {"A": TPL_A, "B": TPL_B}


def true_centroid(who, cx, cy, h=PH):
    return TPL[who].mean(axis=0) * h + np.array([cx, cy])


def det(who, cx, cy, rng, h=PH, conf=0.9, visible=None, jitter=1.0):
    """One YOLO-like detection (keypoints, confidence, bbox xywh)."""
    tpl = TPL[who]
    kp = tpl * h + np.array([cx, cy]) + rng.normal(0, jitter, tpl.shape)
    c = np.full(17, conf, dtype=np.float64)
    if visible is not None:
        m = np.zeros(17, bool)
        m[list(visible)] = True
        c[~m] = 0.08
    x0 = cx + (tpl[:, 0].min() - 0.04) * h
    x1 = cx + (tpl[:, 0].max() + 0.04) * h
    bbox = np.array([x0, cy - 0.5 * h, x1 - x0, h], dtype=np.float64)
    return kp.astype(np.float64), c, bbox


def union_det(ax, bx, cy, rng, h=PH):
    """One YOLO box over both bodies: union bbox, a mixed skeleton (mean of the two)."""
    ka, ca, ba = det("A", ax, cy, rng, h)
    kb, cb, bb = det("B", bx, cy, rng, h)
    x0 = min(ba[0], bb[0])
    x1 = max(ba[0] + ba[2], bb[0] + bb[2])
    return 0.5 * (ka + kb), np.full(17, 0.6), np.array([x0, cy - 0.5 * h, x1 - x0, h])


# ----------------------------------------------------------------------------------------------
class Rig:
    def __init__(self, images=False, seed=0, max_dancers=None):
        DancerTrack._id_counter = 0
        s = ProcessingSettings(confidence=C.YOLO_CONFIDENCE, imgsz=C.YOLO_IMGSZ, use_fp16=False,
                               enhance_enabled=False, enhance_lite=False, enhance_force=False,
                               person_height_px=PH, use_gpu_path=False)
        if max_dancers is not None:
            s.max_dancers = max_dancers
        self.p = FrameProcessor(model=None, settings=s)
        self.t = 0.0
        self.p.set_output_clock(lambda: self.t)
        self.tmp = tempfile.mkdtemp(prefix="wd_contact_")
        lg = self.p.tracker.logger
        lg.start_session(self.tmp)
        self.events = []
        orig = lg.log

        def log(event, data=None, *a, **k):
            self.events.append((self.frame, event, dict(data or {})))
            return orig(event, data, *a, **k)
        lg.log = log
        self.frame = 0
        self.rng = np.random.default_rng(seed)
        self.images = images
        self.rows = []
        if images:
            empties = [self._render([]) for _ in range(80)]
            self.p.set_clean_plate(CleanPlate.from_frames(empties[:40], source="synthetic"))
            for g in empties:                       # MOG2 warm-up on the empty wall (>= 60 frames)
                self.p._feed_motion_detectors(g)

    def _render(self, bodies):
        g = np.full((HF, W), 14.0, np.float32) + self.rng.normal(0, 1.0, (HF, W)).astype(np.float32)
        g = np.clip(g, 0, 255).astype(np.uint8)
        for who, cx, cy in bodies:                  # later bodies are in front
            wbody = 0.30 if who == "A" else 0.40
            cv2.ellipse(g, (int(cx), int(cy + 0.06 * PH)), (int(wbody * PH / 2), int(0.42 * PH)),
                        0, 0, 360, 42, -1)
            cv2.circle(g, (int(cx), int(cy - 0.43 * PH)), int(0.07 * PH), 42, -1)
        return g

    def step(self, dets, truth, bodies=None, phase=""):
        """dets: raw YOLO-like detections; truth: {who: (cx, cy)} box centres of the dancers on the wall."""
        self.frame += 1
        self.t = self.frame / FPS
        n_raw = len(dets)
        self.p._guard_person_height(dets, 1.0)
        dets = self.p._filter_duplicate_detections(dets, effective_person_height=PH)
        gray = None
        if self.images:
            gray = self._render(bodies if bodies is not None else [(w, x, y) for w, (x, y) in truth.items()])
        space = dict(person_height=PH, scale=1.0, pad_x=0, pad_y=0, roi_x=0, roi_y=0, frame_width=W)
        reported = self.p.replay_gpu_cached(dets, space, gray, W, HF, self.frame, {})
        em = [(int(o.track_id), o.slot_state, float(o.smoothed_centroid[0]), float(o.smoothed_centroid[1]))
              for o in self.p.last_emitted]
        tc = {w: true_centroid(w, x, y) for w, (x, y) in truth.items()}
        trk = [(t.track_id, t.time_since_update, bool(t._occluded), t.hits, t._frames_since_skeleton,
                round(float(t._warmup_score), 1), self.p.tracker.last_emit_reasons.get(t.track_id))
               for t in self.p.tracker.tracks]
        self.rows.append(dict(f=self.frame, t=self.t, phase=phase, n_raw=n_raw, n_det=len(dets),
                              rep=[t.track_id for t in reported], em=em, truth=tc, trk=trk,
                              keys={s.sid: s.key for s in self.p._slots.slots} if self.p._slots else {}))
        return em


# ----------------------------------------------------------------------------------------------
def assign(em, truth):
    """slot -> dancer for this frame: greedy nearest within R_ON, one slot per dancer."""
    pairs = []
    for sid, st, x, y in em:
        for who, c in truth.items():
            pairs.append((float(np.hypot(x - c[0], y - c[1])), sid, who))
    out, used_s, used_w = {}, set(), set()
    for d, sid, who in sorted(pairs):
        if d <= R_ON and sid not in used_s and who not in used_w:
            out[who] = (sid, d)
            used_s.add(sid)
            used_w.add(who)
    return out


def longest(flags):
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def report(name, rig, phases, verbose=True):
    rows = rig.rows
    n_true = [len(r["truth"]) for r in rows]
    print(f"\n=== {name}  [{'images' if rig.images else 'logic'}]  frames={len(rows)}")
    strip = "".join(str(min(9, len(r["em"]))) for r in rows)
    states = {}
    for sid in sorted({e[0] for r in rows for e in r["em"]}):
        states[sid] = "".join(
            next(({"live": "L", "coasting": "c", "fg": "f", "belt": "b", "weak": "w"}.get(e[1], "?")
                  for e in r["em"] if e[0] == sid), ".") for r in rows)
    ph_strip = "".join(r["phase"][:1] for r in rows)
    if verbose:
        print("  phase   :", ph_strip)
        print("  n_det   :", "".join(str(r["n_det"]) for r in rows))
        print("  trk rep :", "".join(str(min(9, len(r["rep"]))) for r in rows))
        print("  osc n   :", strip)
        for sid, s in states.items():
            print(f"  slot {sid}  :", s)
    # per phase
    start_map = None
    for ph in phases:
        rr = [r for r in rows if r["phase"] == ph]
        if not rr:
            continue
        two = [len(r["em"]) >= len(r["truth"]) for r in rr]
        cov = []
        offbody = 0
        dupl = 0
        maps = Counter()
        for r in rr:
            a = assign(r["em"], r["truth"])
            cov.append(len(a) == len(r["truth"]))
            for sid, st, x, y in r["em"]:
                dmin = min(float(np.hypot(x - c[0], y - c[1])) for c in r["truth"].values())
                if dmin > R_ON:
                    offbody += 1
            if len(r["em"]) >= 2 and len(a) < len(r["truth"]):
                dupl += 1
            maps[tuple(sorted((w, s[0]) for w, s in a.items()))] += 1
        hole = longest([not c for c in cov])
        hole_n = longest([not t for t in two])
        coast = sum(1 for r in rr for e in r["em"] if e[1] != "live")
        tot_pts = sum(len(r["em"]) for r in rr)
        m = maps.most_common(1)[0][0]
        if start_map is None and len(m) == 2:
            start_map = dict(m)
        print(f"  {ph:<10} {len(rr):3d} fr | ids>=N {100*np.mean(two):5.1f}% | each dancer has its own point "
              f"{100*np.mean(cov):5.1f}% | longest hole {hole/FPS:4.2f}s (count<N {hole_n/FPS:4.2f}s) | "
              f"non-live pts {100*coast/max(1,tot_pts):4.1f}% | off-body pts {offbody} | "
              f"2 pts on 1 dancer {dupl} fr | map {dict(m)}")
    end = [r for r in rows if r["phase"] == phases[-1]][-20:]
    endmaps = Counter(tuple(sorted((w, s[0]) for w, s in assign(r["em"], r["truth"]).items())) for r in end)
    em = dict(endmaps.most_common(1)[0][0]) if endmaps else {}
    swapped = (start_map is not None and len(em) == 2 and em != start_map)
    print(f"  slot->dancer before {start_map}  after {em}  => "
          f"{'ID SWAP' if swapped else ('same ids' if em == start_map else 'INCOMPLETE')}")
    ev = Counter(e for _f, e, _d in rig.events
                 if e in ("TRACK_MERGED", "NEW_TRACK", "FORCE_UPDATE", "FALLBACK_UPDATE", "RESURRECT",
                          "DORMANT", "DUPLICATE_IGNORED", "AMBIGUOUS_IGNORED", "KILL_SHADOW", "ANTI_MERGE",
                          "MATCH_REJECTED", "OCCLUDED", "MOTION_BRIDGE", "MOTION_SYNTHETIC_DET",
                          "GHOST_FROZEN_SUPPRESSED", "DISPLACEMENT_GATE", "MAHALANOBIS_GATE",
                          "WARMUP_SLOW_DUP_SUPPRESSED"))
    print("  tracker events:", dict(ev))
    for f, e, d in rig.events:
        if e in ("TRACK_MERGED", "NEW_TRACK", "RESURRECT", "DORMANT", "KILL_SHADOW") and f > 20:
            print(f"    f{f:4d} t={f/FPS:5.2f}s {e} {d}")
    sev = [(f, d) for f, e, d in rig.events if e == "SLOT_EVENT"]
    for f, d in sev:
        print(f"    f{f:4d} t={f/FPS:5.2f}s SLOT {d}")
    c = rig.p._slots.counters if rig.p._slots else {}
    print("  slot counters:", {k: v for k, v in c.items() if v})
    print("  tracker ids minted:", DancerTrack._id_counter)
    return rows


def first_two_after(rows, phase):
    """Seconds from the first frame of `phase` until each dancer has its own point for 5 frames in a row."""
    rr = [r for r in rows if r["phase"] == phase]
    run = 0
    for i, r in enumerate(rr):
        ok = len(assign(r["em"], r["truth"])) == len(r["truth"])
        run = run + 1 if ok else 0
        if run >= 5:
            return (i - 4) / FPS
    return None


# ----------------------------------------------------------------------------------------------
def sway(i, amp=0.04 * PH, hz=0.5):
    return amp * np.sin(2 * np.pi * hz * i / FPS)


def prelude(rig, n=80):
    """Both dancers apart (3+ heights), walking a little: tracks established, slots bound with reputation."""
    for i in range(n):
        ax = 380 + 100 * i / n
        bx = 900 - 100 * i / n
        rig.step([det("A", ax, YC, rig.rng), det("B", bx, YC, rig.rng)], {"A": (ax, YC), "B": (bx, YC)},
                 phase="prelude")
    return 480.0, 800.0


def move(rig, a0, b0, a1, b1, n, phase, dets_fn=None):
    for i in range(1, n + 1):
        ax = a0 + (a1 - a0) * i / n
        bx = b0 + (b1 - b0) * i / n
        d = dets_fn(ax, bx, i) if dets_fn else [det("A", ax, YC, rig.rng), det("B", bx, YC, rig.rng)]
        rig.step(d, {"A": (ax, YC), "B": (bx, YC)}, phase=phase)


def contact_scenario(name, mode, d_h, images, end="orig", contact_s=5.0, seed=0):
    """mode: 'one_front' (YOLO returns only A's box), 'one_union' (one box over both),
    'two' (both boxes), 'two_weakB' (B partly hidden: 6 keypoints -> low quality)."""
    rig = Rig(images=images, seed=seed)
    a, b = prelude(rig)
    xc = 640.0
    ac, bc = xc - d_h * PH / 2, xc + d_h * PH / 2
    move(rig, a, b, ac, bc, 20, "approach")
    for i in range(int(contact_s * FPS)):
        s = sway(i)
        ax, bx = ac + s, bc + s
        if mode == "one_front":
            dets = [det("A", ax, YC, rig.rng)]
        elif mode == "one_union":
            dets = [union_det(ax, bx, YC, rig.rng)]
        elif mode == "two":
            dets = [det("A", ax, YC, rig.rng), det("B", bx, YC, rig.rng)]
        elif mode == "two_weakB":
            dets = [det("A", ax, YC, rig.rng),
                    det("B", bx, YC, rig.rng, conf=0.55, visible=(0, 1, 3, 5, 7, 9))]
        rig.step(dets, {"A": (ax, YC), "B": (bx, YC)}, bodies=[("B", bx, YC), ("A", ax, YC)], phase="contact")
    if end == "orig":
        move(rig, ac, bc, 440.0, 840.0, 40, "separate")
        fa, fb = 440.0, 840.0
    else:   # they leave on swapped sides
        move(rig, ac, bc, 840.0, 440.0, 40, "separate")
        fa, fb = 840.0, 440.0
    for i in range(80):
        s = sway(i, amp=0.15 * PH)
        rig.step([det("A", fa + s, YC, rig.rng), det("B", fb - s, YC, rig.rng)],
                 {"A": (fa + s, YC), "B": (fb - s, YC)}, phase="after")
    rows = report(name, rig, ["prelude", "approach", "contact", "separate", "after"])
    ra = first_two_after([r for r in rows if r["phase"] in ("separate", "after")]
                         and [dict(r, phase="x") if r["phase"] in ("separate", "after") else r for r in rows], "x")
    print(f"  re-acquire: each dancer has its own point again {ra if ra is None else round(ra, 2)} s "
          f"after the separation starts")
    return rig


def crossing_scenario(name, images, end="swap", occl_h=0.38, partial_h=None, seed=0):
    """A walks right, B walks left; A passes in front.  While |xA - xB| < occl_h x PH only A is detected
    (1 s at the chosen speed).  partial_h: between occl_h and partial_h B is a low-quality detection
    (6 keypoints) -- the pre-tracker shadow suppression then removes it."""
    rig = Rig(images=images, seed=seed)
    for i in range(60):       # both in place (swaying), 520 px apart
        s = sway(i, amp=0.15 * PH)
        rig.step([det("A", 380 + s, YC, rig.rng), det("B", 900 - s, YC, rig.rng)],
                 {"A": (380 + s, YC), "B": (900 - s, YC)}, phase="prelude")
    v = occl_h * PH / FPS      # px / frame each: |sep| < occl for 2*occl/(2v) = 1 s
    ax, bx = 380.0, 900.0
    turned = False
    n = 0
    while n < 400:
        n += 1
        if end == "orig" and not turned and ax >= bx:      # touch, fully overlapped, and go back
            turned = True
        if end == "orig" and turned:
            ax -= v
            bx += v
        else:
            ax += v
            bx -= v
        sep = abs(ax - bx) / PH
        if sep < occl_h:
            dets = [det("A", ax, YC, rig.rng)]
            ph = "occluded"
        elif partial_h is not None and sep < partial_h:
            dets = [det("A", ax, YC, rig.rng), det("B", bx, YC, rig.rng, conf=0.55, visible=(0, 1, 3, 5, 7, 9))]
            ph = "partial"
        else:
            dets = [det("A", ax, YC, rig.rng), det("B", bx, YC, rig.rng)]
            ph = "walk_in" if not any(r["phase"] == "occluded" for r in rig.rows) else "walk_out"
        rig.step(dets, {"A": (ax, YC), "B": (bx, YC)}, bodies=[("B", bx, YC), ("A", ax, YC)], phase=ph)
        if ph == "walk_out" and abs(ax - bx) > 3.0 * PH:
            break
    for i in range(60):
        s = sway(i, amp=0.15 * PH)
        rig.step([det("A", ax + s, YC, rig.rng), det("B", bx - s, YC, rig.rng)],
                 {"A": (ax + s, YC), "B": (bx - s, YC)}, phase="after")
    phases = ["prelude", "walk_in"] + (["partial"] if partial_h else []) + ["occluded", "walk_out", "after"]
    rows = report(name, rig, phases)
    occ_end = max(i for i, r in enumerate(rows) if r["phase"] == "occluded")
    tail = [dict(r, phase="x") for r in rows[occ_end + 1:]]
    ra = first_two_after(tail, "x")
    print(f"  occluded frames: {sum(r['phase'] == 'occluded' for r in rows)}; re-acquire: each dancer has its "
          f"own point again {ra if ra is None else round(ra, 2)} s after the occlusion ends")
    return rig


def dedupe_cases():
    print("\n=== detection dedupe (_filter_duplicate_detections, pipeline.py:2387), PH =", PH)
    rig = Rig()
    rng = rig.rng
    f = rig.p._filter_duplicate_detections

    def show(label, dets):
        out = f([(k.copy(), c.copy(), b.copy()) for k, c, b in dets], effective_person_height=PH)
        cs = []
        for k, c, b in out:
            m = c > C.KEYPOINT_CONFIDENCE
            cs.append(np.average(k[m], axis=0, weights=c[m]).round(1).tolist())
        ins = []
        for k, c, b in dets:
            m = c > C.KEYPOINT_CONFIDENCE
            ins.append(np.average(k[m], axis=0, weights=c[m]).round(1).tolist())
        print(f"  {label:<62} in {len(dets)} -> out {len(out)}  centroids in {ins} out {cs}")

    for d in (0.05, 0.08, 0.10, 0.15, 0.25, 0.30):
        a, b = det("A", 600, YC, rng, jitter=0), det("B", 600 + d * PH, YC, rng, jitter=0)
        iou = rig.p._compute_iou(a[2], b[2])
        show(f"two standing bodies {d:.2f} h apart (box IoU {iou:.2f})", [a, b])
    a = det("A", 600, YC, rng, jitter=0)
    # a carried / horizontal dancer: box 1.0 h wide, 0.25 h high, at shoulder height of the carrier
    kb, cb, _ = det("B", 600, YC, rng, jitter=0)
    kb = np.stack([600 + (kb[:, 1] - YC), (YC - 0.35 * PH) + (kb[:, 0] - 600) * 0.6], axis=1)   # rotated 90 deg
    bb = np.array([600 - 0.5 * PH, YC - 0.35 * PH - 0.125 * PH, 1.0 * PH, 0.25 * PH])
    show("lift: horizontal carried body, box height 0.25 h (< 0.3 h)", [a, (kb, cb, bb)])
    bb2 = np.array([600 - 0.5 * PH, YC - 0.35 * PH - 0.175 * PH, 1.0 * PH, 0.35 * PH])
    show("lift: horizontal carried body, box height 0.35 h", [a, (kb, cb, bb2)])
    # tucked dancer held in front of a standing one: 0.4 x 0.4 h box inside the carrier's box
    kt, ct, _ = det("B", 600, YC, rng, jitter=0)
    kt = 600 + (kt - np.array([600, YC])) * 0.4
    kt[:, 1] += YC - 600
    bt = np.array([600 - 0.2 * PH, YC - 0.2 * PH, 0.4 * PH, 0.4 * PH])
    show("lift: tucked body 0.4 x 0.4 h inside the carrier's box", [a, (kt, ct, bt)])
    # half-hidden partner (6 keypoints) at various distances
    for d in (0.4, 0.8, 1.2, 1.45, 1.6):
        b = det("B", 600 + d * PH, YC, rng, conf=0.55, visible=(0, 1, 3, 5, 7, 9), jitter=0)
        show(f"half-hidden partner (6 kpts) {d:.2f} h from a well-seen dancer", [a, b])
    for d in (0.4, 1.0):
        b = det("B", 600 + d * PH, YC, rng, conf=0.45, jitter=0)
        show(f"dim partner (17 kpts, mean conf 0.45) {d:.2f} h from a well-seen dancer", [a, b])


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    np.set_printoptions(suppress=True)
    print("defaults: conf", C.YOLO_CONFIDENCE, "imgsz", C.YOLO_IMGSZ, "max_dancers", C.IDENTITY_SLOTS_MAX_DANCERS,
          "coast_s", C.IDENTITY_SLOTS_COAST_S, "smart_hold", C.IDENTITY_SLOTS_SMART_HOLD,
          "static_guard", C.IDENTITY_SLOTS_STATIC_GUARD, "intermittent", C.TRACK_WARMUP_INTERMITTENT_ENABLED,
          "swap_correctors", C.TRACKER_SWAP_CORRECTORS, "max_age", C.TRACKER_MAX_AGE)
    if which in ("all", "dedupe"):
        dedupe_cases()
    for images in ((False, True) if which in ("all",) else ((which.endswith("img")),)):
        if which in ("all", "a", "aimg"):
            contact_scenario("(a1) contact 5 s, 0.30 h apart, YOLO = ONE box (front dancer A only)",
                             "one_front", 0.30, images)
            contact_scenario("(a2) contact 5 s, 0.30 h apart, YOLO = ONE union box (mixed skeleton)",
                             "one_union", 0.30, images)
            contact_scenario("(a3) as (a1) but they leave on SWAPPED sides", "one_front", 0.30, images, end="swap")
        if which in ("all", "b", "bimg"):
            for d in (0.50, 0.40, 0.30, 0.25, 0.15):
                contact_scenario(f"(b) contact 5 s, TWO boxes, {d:.2f} h apart", "two", d, images)
            contact_scenario("(b-weak) contact 5 s, TWO boxes 0.40 h apart, B half-hidden (6 kpts)",
                             "two_weakB", 0.40, images)
        if which in ("all", "c", "cimg"):
            crossing_scenario("(c1) crossing, 1 s single detection, they continue (swapped sides)", images, "swap")
            crossing_scenario("(c2) crossing, 1 s single detection, they turn back (original sides)", images, "orig")
            crossing_scenario("(c3) as (c1) + B half-hidden between 0.38 and 0.9 h", images, "swap", partial_h=0.9)
