#!/usr/bin/env python
"""Sweeps on top of contact_repro.py (same shipped chain); one line per run."""
import io, sys, contextlib, time
import numpy as np
import contact_repro as R
from contact_repro import Rig, det, union_det, prelude, move, sway, assign, PH, YC, FPS
from core.tracker import DancerTrack


def stats(rig, skip_phase="prelude"):
    rows = [r for r in rig.rows if r["phase"] != skip_phase]
    cov = [len(assign(r["em"], r["truth"])) == len(r["truth"]) for r in rows]
    cnt = [len(r["em"]) >= len(r["truth"]) for r in rows]
    nonlive = sum(1 for r in rows for e in r["em"] if e[1] != "live")
    pts = sum(len(r["em"]) for r in rows)
    pre = [r for r in rig.rows if r["phase"] == skip_phase][-10:]
    end = rig.rows[-10:]
    def m(rr):
        from collections import Counter
        c = Counter(tuple(sorted((w, s[0]) for w, s in assign(r["em"], r["truth"]).items())) for r in rr)
        return dict(c.most_common(1)[0][0])
    m0, m1 = m(pre), m(end)
    ev = [e for _f, e, _d in rig.events]
    merged = [(f, d.get("mode", "colocated")) for f, e, d in rig.events if e == "TRACK_MERGED"]
    slot_lost = [(f, d["why"]) for f, e, d in rig.events if e == "SLOT_EVENT" and d.get("ev") == "lost"]
    return dict(hole=R.longest([not c for c in cov]) / FPS, hole_cnt=R.longest([not c for c in cnt]) / FPS,
                miss=sum(not c for c in cov) / FPS, nonlive=100.0 * nonlive / max(1, pts),
                swap=(len(m1) == 2 and m0 != m1), minted=DancerTrack._id_counter,
                merged=merged, slot_lost=slot_lost, ign=ev.count("DUPLICATE_IGNORED") + ev.count("AMBIGUOUS_IGNORED"))


def line(label, rig):
    s = stats(rig)
    print(f"  {label:<66} longest stretch a dancer has no own point {s['hole']:5.2f}s (count<N {s['hole_cnt']:5.2f}s) "
          f"total {s['miss']:5.2f}s | non-live {s['nonlive']:4.1f}% | swap {str(s['swap']):5} | ids {s['minted']} | "
          f"trk merge {[(f, m) for f, m in s['merged']]} | slot lost {s['slot_lost']} | dets ignored {s['ign']}")


def contact(mode, d_h, contact_s, images, seed=0, approach_n=20, stay_h=None, stay_s=5.0):
    rig = Rig(images=images, seed=seed)
    a, b = prelude(rig)
    xc = 640.0
    ac, bc = xc - d_h * PH / 2, xc + d_h * PH / 2
    move(rig, a, b, ac, bc, approach_n, "approach")
    for i in range(int(contact_s * FPS)):
        s = sway(i)
        ax, bx = ac + s, bc + s
        if mode == "one_front":
            dets = [det("A", ax, YC, rig.rng)]
        elif mode == "one_union":
            dets = [union_det(ax, bx, YC, rig.rng)]
        else:
            dets = [det("A", ax, YC, rig.rng), det("B", bx, YC, rig.rng)]
        rig.step(dets, {"A": (ax, YC), "B": (bx, YC)}, bodies=[("B", bx, YC), ("A", ax, YC)], phase="contact")
    if stay_h is not None:      # they let go but stay close, both fully detected
        a2, b2 = xc - stay_h * PH / 2, xc + stay_h * PH / 2
        move(rig, ac, bc, a2, b2, 10, "letgo")
        for i in range(int(stay_s * FPS)):
            s = sway(i)
            rig.step([det("A", a2 + s, YC, rig.rng), det("B", b2 + s, YC, rig.rng)],
                     {"A": (a2 + s, YC), "B": (b2 + s, YC)}, phase="close")
        ac, bc = a2, b2
    move(rig, ac, bc, 440.0, 840.0, 40, "separate")
    for i in range(60):
        s = sway(i, amp=0.15 * PH)
        rig.step([det("A", 440 + s, YC, rig.rng), det("B", 840 - s, YC, rig.rng)],
                 {"A": (440 + s, YC), "B": (840 - s, YC)}, phase="after")
    return rig


def crossing(occ_s, end, images, seed=0, occl_h=0.38):
    rig = Rig(images=images, seed=seed)
    for i in range(60):
        s = sway(i, amp=0.15 * PH)
        rig.step([det("A", 380 + s, YC, rig.rng), det("B", 900 - s, YC, rig.rng)],
                 {"A": (380 + s, YC), "B": (900 - s, YC)}, phase="prelude")
    v = occl_h * PH / (FPS * occ_s)
    ax, bx = 380.0, 900.0
    turned, seen_occ, n = False, False, 0
    while n < 1500:
        n += 1
        if end == "orig" and not turned and ax >= bx:
            turned = True
        if end == "orig" and turned:
            ax -= v; bx += v
        else:
            ax += v; bx -= v
        sep = abs(ax - bx) / PH
        if sep < occl_h:
            dets = [det("A", ax, YC, rig.rng)]; ph = "occluded"; seen_occ = True
        else:
            dets = [det("A", ax, YC, rig.rng), det("B", bx, YC, rig.rng)]
            ph = "walk_out" if seen_occ else "walk_in"
        rig.step(dets, {"A": (ax, YC), "B": (bx, YC)}, bodies=[("B", bx, YC), ("A", ax, YC)], phase=ph)
        if ph == "walk_out" and abs(ax - bx) > 2.5 * PH:
            break
    for i in range(40):
        s = sway(i, amp=0.15 * PH)
        rig.step([det("A", ax + s, YC, rig.rng), det("B", bx - s, YC, rig.rng)],
                 {"A": (ax + s, YC), "B": (bx - s, YC)}, phase="after")
    return rig


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(*a, **k)


if __name__ == "__main__":
    which = sys.argv[1:] or ["s1", "s2", "s3", "s4", "s5", "s6"]
    t0 = time.time()
    import os
    for images in [t == 'images' for t in os.environ.get('TIERS', 'logic,images').split(',')]:
        tier = "images" if images else "logic"
        print(f"\n##### tier = {tier}")
        if "s1" in which:
            print(" S1 one box (front dancer only), 0.30 h apart, contact duration:")
            for cs in (3.0, 5.0, 10.0):
                line(f"contact {cs:.0f} s", quiet(contact, "one_front", 0.30, cs, images))
        if "s2" in which:
            print(" S2 one box, 5 s, contact distance / approach speed:")
            for d in (0.20, 0.30, 0.40, 0.50):
                line(f"front-only box, {d:.2f} h, approach 1 s", quiet(contact, "one_front", d, 5.0, images))
            for d in (0.30, 0.50):
                line(f"front-only box, {d:.2f} h, slow approach 4 s", quiet(contact, "one_front", d, 5.0, images, approach_n=80))
            for d in (0.30, 0.50):
                line(f"union box, {d:.2f} h, approach 1 s", quiet(contact, "one_union", d, 5.0, images))
        if "s3" in which:
            print(" S3 TWO clean boxes, 5 s, contact distance:")
            for d in (0.15, 0.25, 0.28, 0.29, 0.30, 0.35):
                line(f"two boxes, {d:.2f} h", quiet(contact, "two", d, 5.0, images))
        if "s4" in which:
            print(" S4 crossing, duration of the single-detection window:")
            for occ in [float(v) for v in os.environ.get('OCC', '0.5,1.0,1.5,2.0,3.0').split(',')]:
                for end in ("swap", "orig"):
                    line(f"crossing {occ:.1f} s occluded, end={end}", quiet(crossing, occ, end, images))
        if "s5" in which:
            print(" S5 after a 5 s one-box embrace they let go but stay close for 5 s, both fully detected:")
            for sh in [float(v) for v in os.environ.get('STAY', '0.45,0.60,0.80,0.90').split(',')]:
                line(f"stay {sh:.2f} h apart, two clean boxes", quiet(contact, "one_front", 0.30, 5.0, images, stay_h=sh))
        if "s6" in which:
            print(" S6 seeds (keypoint jitter / image noise), crossing 1.0 s:")
            for end in ("swap", "orig"):
                for seed in range(1, 6):
                    line(f"crossing 1.0 s, end={end}, seed {seed}", quiet(crossing, 1.0, end, images, seed=seed))
        print(f"  [{time.time()-t0:.0f} s elapsed]")
