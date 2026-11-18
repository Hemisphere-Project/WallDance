"""identity-output#2 / #3 / #11 on IdentitySlots alone, with the params the pipeline builds
(pipeline.py:1488-1497: static guard + yield ON, smart hold ON, coast_s 2.0, max_dancers 2)."""
import sys
sys.dont_write_bytecode = True
import numpy as np
from core.identity_slots import IdentitySlots, SlotParams, SlotCandidate

H = 150.0          # dancer height, px
DT = 0.05
BOUNDS = (0.0, 0.0, 1500.0, 600.0)

def mk():
    return IdentitySlots(SlotParams(max_dancers=2, coast_s=2.0, static_guard=True, static_yield=True,
                                    smart_hold=True))

def cand(key, x, y, hits, fss=0, tsu=0):
    return SlotCandidate(key=key, x=x, y=y, w=0.4 * H, h=H, hits=hits, fss=fss, tsu=tsu, conf=0.6)

def ids(outs):
    return {o.slot_id: o.state for o in outs}

def warm(sl, frames=80, end_sep=0.2):
    """A walks slowly; B starts 4 h to its right and walks up to end_sep x h from A (both live,
    fresh skeletons, reputation + travel earned)."""
    t = 0.0
    for i in range(frames):
        t = i * DT
        xa_i = 300.0 + 2.0 * i
        sep = 4.0 + (end_sep - 4.0) * i / (frames - 1)
        outs = sl.update([cand(1, xa_i, 300.0, 20 + i), cand(2, xa_i + sep * H, 300.0, 20 + i)], t, bounds=BOUNDS)
    return t, outs

def events(sl, t, tag):
    for e in sl.events:
        print(f"    t={t:5.2f} {tag} event {e}")

print("=== A. contact: B's skeleton is lost while B is 0.2 h from A (tracker keeps predicting B, tsu>0)")
sl = mk(); t, outs = warm(sl)
xa = 300.0 + 2.0 * 79
print(f"  after warm-up t={t:.2f}: {ids(outs)}")
gone_at = None
for k in range(1, 41):                       # 2 s of contact, B occluded at 0.2 h from A
    t += DT
    outs = sl.update([cand(1, xa, 300.0, 100 + k), cand(2, xa + 0.2 * H, 300.0, 100, fss=k, tsu=k)], t, bounds=BOUNDS)
    events(sl, t, "A")
    if 2 not in ids(outs) and gone_at is None:
        gone_at = k * DT
print(f"  id 2 left the output {gone_at} s after the occlusion began (merge_coast_hold_s 0.5, the granted hold was 2.0 s)")
for sep in (0.3, 0.45, 0.55):
    back = None
    for k in range(1, 21):                   # B visible again (same tracker id, fresh skeleton) at sep x h from A
        t += DT
        outs = sl.update([cand(1, xa, 300.0, 200 + k), cand(2, xa + sep * H, 300.0, 120 + k, fss=0, tsu=0)], t, bounds=BOUNDS)
        events(sl, t, "A")
        if 2 in ids(outs) and back is None:
            back = k
    print(f"  B back with a fresh skeleton at {sep:.2f} h from A for 1 s: "
          + (f"id 2 re-emitted after {back} frame(s)" if back else "id 2 NOT re-emitted (dup_bind_h 0.5)"))

print("=== A2. B's tracker track dies while B is 0.4 h from A (outside merge_h 0.25, inside dup_bind_h 0.5); YOLO re-finds B there as a NEW track")
sl = mk(); t, outs = warm(sl, end_sep=0.4)
xa = 300.0 + 2.0 * 79
gone = None
for k in range(1, 121):                      # 6 s: A live; B first unseen 1 s, then a new tracker id 9 with fresh skeletons
    t += DT
    c = [cand(1, xa, 300.0, 100 + k)]
    if k > 20:
        c.append(cand(9, xa + 0.4 * H, 300.0, k - 20, fss=0, tsu=0))
    outs = sl.update(c, t, bounds=BOUNDS)
    events(sl, t, "A2")
    if 2 not in ids(outs) and gone is None:
        gone = k * DT
print(f"  id 2 left the output {gone} s after the loss; at t+6 s emitted = {ids(outs)} (track 9: {120-20} hits, fresh skeleton, 0.4 h from A)")
print("=== B. both dancers lose their skeleton while 0.2 h apart (lift / embrace): two non-live slots")
sl = mk(); t, outs = warm(sl)
xa = 300.0 + 2.0 * 79
for k in range(1, 6):
    t += DT
    c = [cand(1, xa, 300.0, 100, fss=k, tsu=k), cand(2, xa + 0.2 * H, 300.0, 100, fss=k, tsu=k)]
    outs = sl.update(c, t, bounds=BOUNDS)
    events(sl, t, "B")
    print(f"    t={t:.2f} emitted {ids(outs)}")

print("=== C. control: both keep a fresh skeleton at 0.2 h for 3 s (the documented 'duo in contact keeps both ids')")
sl = mk(); t, outs = warm(sl)
lost = False
for k in range(1, 61):
    t += DT
    outs = sl.update([cand(1, xa, 300.0, 100 + k), cand(2, xa + 0.2 * H, 300.0, 100 + k)], t, bounds=BOUNDS)
    lost = lost or len(outs) < 2
print(f"  two ids throughout: {not lost}")

print("=== D. #11: slot velocity after a gap (dt clamped to max_dt_s 0.25 s)")
for label, stall in (("credited camera stall 1.7 s (D34: t paused, t_motion runs)", True),
                     ("main-loop freeze 1.7 s (both clocks run)", False)):
    sl = IdentitySlots(SlotParams(max_dancers=1, coast_s=2.0, smart_hold=True, static_guard=True, static_yield=True))
    v_true = 0.3 * H                         # px/s, toward the right border: under edge_out_speed_h 0.5 h/s
    x, t, tm = 1255.0, 0.0, 0.0
    for i in range(60):                      # 3 s live, earns the full hold
        t += DT; tm += DT; x += v_true * DT
        outs = sl.update([cand(1, x, 300.0, 20 + i)], t, t_motion=tm, bounds=BOUNDS)
    s = sl.slots[0]
    print(f"  {label}: before, slot vel {np.linalg.norm(s.vel)/H:.2f} h/s (true 0.30), x={x:.0f} (border 1500, margin 0.25 h = {0.25*H:.0f} px)")
    gap = 1.7
    tm += gap; x += v_true * gap
    t += DT if stall else gap
    outs = sl.update([cand(1, x, 300.0, 90)], t, t_motion=tm, bounds=BOUNDS)
    events(sl, t, "D")
    print(f"    first frame back: slot vel {np.linalg.norm(s.vel)/H:.2f} h/s, OSC velocity (One-Euro) {np.hypot(outs[0].vx, outs[0].vy)/H:.2f} h/s, state {outs[0].state}")
    # the track is lost on the next frame (YOLO misses after the stall)
    t += DT; tm += DT
    outs = sl.update([], t, t_motion=tm, bounds=BOUNDS)
    print(f"    track lost on the next frame: granted hold {s.hold_s} s (2.0 = earned, 0.3 = 'leaving across the border')")
    n = 0
    while outs and n < 100:
        t += DT; tm += DT; n += 1
        outs = sl.update([], t, t_motion=tm, bounds=BOUNDS)
    print(f"    id emitted for {n * DT:.2f} s after the loss")
# control without a gap
sl = IdentitySlots(SlotParams(max_dancers=1, coast_s=2.0, smart_hold=True, static_guard=True, static_yield=True))
x, t = 1255.0, 0.0
for i in range(60 + 34):
    t += DT; x += 0.3 * H * DT
    sl.update([cand(1, x, 300.0, 20 + i)], t, bounds=BOUNDS)
t += DT; sl.update([], t, bounds=BOUNDS)
print(f"  control, same walk without a gap, lost at x={x:.0f}: granted hold {sl.slots[0].hold_s} s")

print("=== E. #11: main-loop freeze with a fast dancer (jump check not widened: no credited silence)")
for v_h, gap in ((2.0, 1.2), (2.0, 1.8), (2.5, 2.2)):
    sl = IdentitySlots(SlotParams(max_dancers=1, coast_s=2.0, smart_hold=True, static_guard=True, static_yield=True))
    x, t = 200.0, 0.0
    for i in range(40):
        t += DT; x += v_h * H * DT
        outs = sl.update([cand(1, x, 300.0, 20 + i)], t, bounds=BOUNDS)
    t += gap; x += v_h * H * gap
    seq = []
    for k in range(8):
        outs = sl.update([cand(1, x, 300.0, 70 + k)], t, bounds=BOUNDS)
        seq.append((len(outs), [e["ev"] + (":" + e.get("why", "") if e.get("why") else "") for e in sl.events]))
        t += DT; x += v_h * H * DT
    print(f"  {v_h} h/s, freeze {gap} s (moved {v_h*gap:.1f} h): ids emitted per frame after it / events: {seq}")

print("=== F. #3: foreground veto leaves no trace (fake FgFrame: valid, support 0.05 everywhere)")
class Fg:
    valid = True; blobs = []
    def support(self, x, y, h, wfrac=0.45): return 0.05
sl = mk(); n_ev = 0; emitted = 0
for i in range(200):                         # 10 s of a YOLO-confirmed, fresh-skeleton, moving track at conf 0.9
    c = cand(7, 400.0 + 3.0 * i, 300.0, 30 + i); c.conf = 0.9
    outs = sl.update([c], i * DT, bounds=BOUNDS, fg=Fg())
    n_ev += len(sl.events); emitted += len(outs)
print(f"  10 s: frames with an id {emitted}, SLOT_EVENTs {n_ev}, counters {{'fg_veto': {sl.counters.get('fg_veto')}, 'entries': {sl.counters['entries']}}}")
