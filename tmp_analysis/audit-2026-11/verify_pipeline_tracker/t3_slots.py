"""tracker#3 at the emitted stream: a duo in contact (both skeletons detected throughout), identity slots ON."""
from chain import *

def run(contact_s, sep_h=0.25):
    p, clock, rec = mk_proc()
    ph = int(H)
    fr = 0
    xa, xb = 500.0, 800.0
    def go(dets):
        nonlocal fr
        gated = FrameProcessor._filter_duplicate_detections(p, dets, effective_person_height=ph)
        step(p, clock, rec, gated, fr); fr += 1
        return len(gated)
    for _ in range(60):
        go([det(xa, 600), det(xb, 600)])
    target = xa + sep_h * H
    while xb > target:
        xb = max(target, xb - 6.0); go([det(xa, 600), det(xb, 600)])
    c0 = fr
    ndet = set()
    for _ in range(int(contact_s * 20)):
        ndet.add(go([det(xa, 600), det(xb, 600)]))
    c1 = fr
    while xb < 800.0:
        xb += 6.0; go([det(xa, 600), det(xb, 600)])
    for _ in range(40):
        go([det(xa, 600), det(xb, 600)])
    counts = {m[0]: m[2] for m in rec.msgs if m[1] == "/walldance/count"}
    one = [k for k in range(c0, fr) if counts[k][0] < 2]
    merged = [e["frame"] for e in p.tracker.logger._buffer if e["event"] == "TRACK_MERGED"]
    states = {}
    for e in p.tracker.logger._buffer:
        if e["event"] == "FRAME_SUMMARY" and e["frame"] >= c0:
            for s in (e["data"].get("slots") or []):
                states.setdefault(s["id"], []).append(s["st"])
    summ = {sid: {st: v.count(st) for st in sorted(set(v))} for sid, v in states.items()}
    print("contact %.1f s at %.2f h (detections reaching the tracker per contact frame: %s): TRACK_MERGED at contact frame %s; "
          "frames with fewer than 2 ids on OSC from contact start to the end: %d (%.2f s)%s; slot states since contact: %s; final ids %s"
          % (contact_s, sep_h, sorted(ndet), [m - c0 + 1 for m in merged], len(one), len(one) / 20.0,
             "" if not one else " [contact-relative frames %d..%d]" % (one[0] - c0, one[-1] - c0), summ, counts[fr - 1]))

run(1.0); run(3.0); run(6.0)
run(3.0, sep_h=0.32)
