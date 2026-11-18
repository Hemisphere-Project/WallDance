"""Throwaway audit script, part 2 (long-stillness regime): MOG2 absorption of a still body, the
MOG2 bridge + hidden follow, the plate absorbing a still dancer then vetoing her re-entry, and the
static YIELD with a moving third body / a moving partner.  CPU only; real classes; synthetic inputs."""
from __future__ import annotations

import sys
from collections import Counter

import numpy as np

sys.path.insert(0, "/tmp/wd_audit_stillness")
import still_chain as S  # noqa: E402
from still_chain import FPS, PH, Scene, detection, make_proc, step, fg_probe, motion_probe, body_box_orig  # noqa: E402
from core.foreground import CleanPlate  # noqa: E402
from core.motion_detector import MotionDetector  # noqa: E402


def mog2_absorption():
    """Real MotionDetector (shipped defaults: var 40, scale 0.5, lr 0.001): a body appears on a settled
    wall and never moves -> after how many frames is it gone from the mask the bridge tiers read?"""
    print("=" * 110)
    print("MOG2 absorbing a still body (core.motion_detector.MotionDetector, shipped defaults)")
    for wall, contrast, label in ((30.0, 30.0, "dark wall 30 DN (low-light: var x2), body +30"),
                                  (30.0, 70.0, "dark wall 30 DN (low-light: var x2), body +70"),
                                  (90.0, 40.0, "wall 90 DN (normal light), body +40"),
                                  (90.0, 100.0, "wall 90 DN (normal light), body +100")):
        sc = Scene(wall=wall)
        md = MotionDetector()
        for _ in range(200):                    # 10 s of empty wall
            md.feed(sc.frame())
        x, y, w, h = body_box_orig(640.0, 360.0)
        first = None
        gone_local = gone_presence = None
        r0 = None
        for k in range(400):
            md.feed(sc.frame([(640.0, 360.0, contrast, PH)]))
            r = md.motion_ratio_in_bbox(x, y, w, h, include_shadows=True, use_clean_mask=False, require_coherence=False)
            if k == 1:
                r0 = r
            if first is None and r > 0.02:
                first = k
            if first is not None and gone_local is None and r < 0.01175:     # tracker local-blob min ratio @ sensitivity 0.55
                gone_local = k
            if first is not None and gone_presence is None and r < 0.00805:  # presence-tier min ratio @ 0.55
                gone_presence = k
        _b, fd = md.frame_diff_blob_in_bbox(x, y, w, h, min_ratio=0.0)
        print("  %-48s share at frame 1: %.3f | below the local-blob ratio after %s frames, below the presence ratio after %s "
              "| frame-diff share (still) %.4f, pair age %d" % (label, r0, gone_local, gone_presence, fd, md._diff_pair_age))


def bridge_hidden(contrast=100.0, wall=90.0, yolo_still_frames=0, name=""):
    """A clearly visible body (MOG2 sees it) stops; YOLO stops `yolo_still_frames` after she stopped; no plate."""
    sc = Scene(wall=wall)
    rng = np.random.default_rng(7)
    proc, clock = make_proc()
    i = 0
    for _ in range(80):
        step(proc, clock, i, sc.frame(), []); i += 1
    for k in range(60):
        x = 100.0 + 540.0 * (k + 1) / 60
        step(proc, clock, i, sc.frame([(x, 360.0, contrast, PH)]), [detection(x, 360.0, rng)]); i += 1
    log = []
    for k in range(400):
        dets = [detection(640.0, 360.0, rng)] if k < yolo_still_frames else []
        em, trk, tm = step(proc, clock, i, sc.frame([(640.0, 360.0, contrast, PH)]), dets)
        log.append(("still", k, em, trk, tm, motion_probe(proc, 640.0, 360.0), None)); i += 1
    print("=" * 110)
    print(name)
    prev = None
    for ph, k, em, trk, tm, mp, _ in log:
        st = ",".join("%d:%s" % e for e in em) or "NONE"
        tr = ",".join("T%d:%s:%s" % (t[0], t[1], t[4]) for t in trk) or "-"
        if (st, tr) != prev:
            print("    still+%.2fs slot[%s] trk[%s] mog %.3f" % (k / FPS, st, ",".join(
                "T%d:%s tsu%d fss%d %s wu%.0f" % t for t in trk) or "-", mp[0]))
            prev = (st, tr)
    lost = next((k for ph, k, em, *_ in log if not em), None)
    print("  point lost at still+%s s (YOLO's last frame: still+%.2f s)" % (
        None if lost is None else round(lost / FPS, 2), (yolo_still_frames - 1) / FPS))


def plate_absorb_then_veto(contrast=5.0, yolo_back_s=30.0, total_s=50.0):
    """Faint still body + plate: YOLO off from 5 s; the unprotected plate absorbs her; YOLO comes back
    (clear skeleton) at `yolo_back_s` while she is STILL motionless."""
    sc = Scene()
    rng = np.random.default_rng(7)
    empties = [sc.frame() for _ in range(80)]
    proc, clock = make_proc(plate=CleanPlate.from_frames(empties[20:60], source="synthetic"))
    i = 0
    for g in empties:
        step(proc, clock, i, g, []); i += 1
    for k in range(60):
        x = 100.0 + 540.0 * (k + 1) / 60
        step(proc, clock, i, sc.frame([(x, 360.0, contrast, PH)]), [detection(x, 360.0, rng)]); i += 1
    print("=" * 110)
    print("plate absorbs a faint still dancer (contrast %.0f DN), then YOLO returns at %.0f s while she is still motionless" % (
        contrast, yolo_back_s))
    prev = None
    n = int(total_s * FPS)
    for k in range(n):
        on = k < 100 or k >= int(yolo_back_s * FPS)
        em, trk, tm = step(proc, clock, i, sc.frame([(640.0, 360.0, contrast, PH)]),
                           [detection(640.0, 360.0, rng)] if on else [])
        i += 1
        st = ",".join("%d:%s" % e for e in em) or "NONE"
        tr = ",".join("T%d:%s" % (t[0], t[1]) for t in trk) or "-"
        if (st, tr) != prev or k % 200 == 199:
            sup = fg_probe(proc, 640.0, 360.0)
            print("    still+%.2fs yolo=%d slot[%s] trk[%s] plate support %s blob %s  veto count %s" % (
                k / FPS, on, st, tr, sup[0], sup[1], proc._slots.counters.get("fg_veto", 0)))
            prev = (st, tr)
    # she finally moves
    for k in range(60):
        x = 640.0 + 6.0 * (k + 1)
        em, trk, tm = step(proc, clock, i, sc.frame([(x, 360.0, contrast, PH)]), [detection(x, 360.0, rng)]); i += 1
        st = ",".join("%d:%s" % e for e in em) or "NONE"
        if st != prev:
            sup = fg_probe(proc, x, 360.0)
            old = fg_probe(proc, 640.0, 360.0)
            print("    move+%.2fs slot[%s] support at her %s | at the spot she left: support %s blob %s" % (
                k / FPS, st, sup[0], old[0], old[1]))
            prev = st
    print("  slot counters:", {k: v for k, v in proc._slots.counters.items() if v})


def duo_yield(third=True, max_dancers=2, name="", churn=False):
    """A motionless (steady, clear YOLO) at (500, 360); B moves on the right; max_dancers = 2.
    third=True: a third body (rigger / extra dancer / moving ghost) walks through the ROI.
    churn=True: instead, B loses YOLO for 1.5 s while moving fast (the tracker mints a new id far away)."""
    sc = Scene()
    rng = np.random.default_rng(11)
    proc, clock = make_proc(max_dancers=max_dancers)
    i = 0
    for _ in range(80):
        step(proc, clock, i, sc.frame(), []); i += 1
    AX, AY, C = 500.0, 360.0, 30.0
    print("=" * 110)
    print(name)
    prev = None
    events = []

    def bpos(k):   # B: slow back-and-forth on the right
        return 950.0 + 120.0 * np.sin(k / 25.0), 300.0 + 40.0 * np.sin(k / 40.0)

    total = 80 + 60 + 700
    for k in range(60 + 700):
        bodies, dets = [], []
        # A walks in for 3 s then stands still
        ax = 100.0 + (AX - 100.0) * min(1.0, (k + 1) / 60.0)
        a_moves_again = k >= 60 + 560            # A moves again 28 s after she stopped
        if a_moves_again:
            ax = AX - 5.0 * (k - (60 + 560))
        bodies.append((ax, AY, C, PH)); dets.append(detection(ax, AY, rng))
        bx, by = bpos(k)
        b_on = True
        if churn and 60 + 200 <= k < 60 + 230:   # B: 1.5 s without YOLO while crossing fast
            b_on = False
        if churn and k >= 60 + 200:
            bx = bx + min(1.0, (k - 260) / 30.0) * 0.0
        if churn and 260 <= k:
            # a fast traverse to the far left-bottom during the dropout, then B stays there moving slowly
            f = min(1.0, (k - 260) / 30.0)
            bx = (1 - f) * bpos(260)[0] + f * 250.0 + (20.0 * np.sin(k / 15.0) if f >= 1 else 0.0)
            by = (1 - f) * bpos(260)[1] + f * 600.0
        bodies.append((bx, by, C, PH))
        if b_on:
            dets.append(detection(bx, by, rng))
        if third and 60 + 200 <= k < 60 + 360:   # the third body crosses right -> left along the bottom, 8 s
            cx = 1200.0 - 6.5 * (k - 260)
            bodies.append((cx, 600.0, C, PH)); dets.append(detection(cx, 600.0, rng))
        em, trk, tm = step(proc, clock, i, sc.frame(bodies), dets); i += 1
        # which emitted point is on A?
        on_a = [t.track_id for t in proc.last_emitted
                if np.hypot(t.smoothed_centroid[0] - ax, t.smoothed_centroid[1] - AY) < 0.5 * PH]
        st = " ".join("%d:%s@%d,%d" % (t.track_id, t.slot_state, t.smoothed_centroid[0], t.smoothed_centroid[1])
                      for t in proc.last_emitted) or "NONE"
        key = (tuple((t.track_id, t.slot_state, int(t.smoothed_centroid[0] // 150)) for t in proc.last_emitted), bool(on_a))
        events.append((k, bool(on_a), len(proc.last_emitted)))
        if key != prev:
            print("    t=%6.2fs (A still since %5.2fs) emitted[%s]  point on A: %s  slot events: %s" % (
                (k - 60) / FPS, max(0.0, (k - 60) / FPS), st, on_a or "NO",
                [(e["ev"], e["slot"], e.get("why") or e.get("key")) for e in proc._slots.events]))
            prev = key
    still = [e for e in events if 60 <= e[0] < 60 + 560]
    no_a = [e[0] for e in still if not e[1]]
    print("  A motionless 28 s with a clear YOLO skeleton EVERY frame: frames without a point on A: %d of %d (%.1f s)%s" % (
        len(no_a), len(still), len(no_a) / FPS,
        "" if not no_a else "; first at still+%.2f s, last at still+%.2f s" % ((no_a[0] - 60) / FPS, (no_a[-1] - 60) / FPS)))
    moved = [e for e in events if e[0] >= 60 + 560]
    back = next((e[0] for e in moved if e[1]), None)
    print("  after A moves again: point back on A at move+%s s" % (None if back is None else round((back - 620) / FPS, 2)))
    print("  slot counters:", {k: v for k, v in proc._slots.counters.items() if v})
    print("  static spots [x, y, h, t_last, kind]:", [[round(v, 1) if isinstance(v, float) else v for v in sp] for sp in proc._slots._spots])


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "mhpd"
    if "m" in which:
        mog2_absorption()
    if "h" in which:
        bridge_hidden(yolo_still_frames=0, name="MOG2 bridge + hidden follow: clear body (wall 90, +100 DN), YOLO lost the moment she stops | no plate")
        bridge_hidden(yolo_still_frames=200, name="same body, YOLO lost after 10 s of stillness (MOG2 has absorbed her) | no plate")
    if "p" in which:
        plate_absorb_then_veto()
    if "d" in which:
        duo_yield(third=True, name="DUO, max_dancers 2: A motionless with steady clear YOLO, B moving, a THIRD body crosses the ROI for 8 s")
    if "c" in which:
        duo_yield(third=False, churn=True, name="DUO, max_dancers 2: A motionless (steady YOLO); B loses YOLO 1.5 s during a fast traverse (tracker id churn)")
    if "n" in which:
        duo_yield(third=True, max_dancers=3, name="same third-body crossing with max_dancers 3 (a free slot exists)")
