import sys
sys.dont_write_bytecode = True
import numpy as np
from core.identity_slots import IdentitySlots, SlotParams, SlotCandidate
H, DT, BOUNDS = 150.0, 0.05, (0.0, 0.0, 1500.0, 600.0)
def mk(): return IdentitySlots(SlotParams(max_dancers=2, coast_s=2.0, static_guard=True, static_yield=True, smart_hold=True))
def cand(key, x, y, hits, fss=0, tsu=0): return SlotCandidate(key=key, x=x, y=y, w=0.4*H, h=H, hits=hits, fss=fss, tsu=tsu, conf=0.6)
def ids(outs): return {o.slot_id: o.state for o in outs}
def warm(sl, frames=80, end_sep=0.2, hold_frames=20):
    t = 0.0
    for i in range(frames + hold_frames):      # approach, then hold_frames still at end_sep (velocity ~0)
        t = i * DT
        j = min(i, frames - 1)
        xa = 300.0 + 2.0 * j
        sep = 4.0 + (end_sep - 4.0) * j / (frames - 1)
        outs = sl.update([cand(1, xa, 300.0, 20 + i), cand(2, xa + sep * H, 300.0, 20 + i)], t, bounds=BOUNDS)
    return t, outs, xa

for sep0 in (0.2, 0.4):
    print(f"--- A'. both still, {sep0} h apart, both live 1 s; then B's track is no longer reported (A stays live)")
    sl = mk(); t, outs, xa = warm(sl, end_sep=sep0)
    t0, gone = t, None
    for k in range(1, 81):
        t += DT
        c = [cand(1, xa, 300.0, 200 + k)]
        if k > 50:   # B re-detected 2.5 s later, where it was, as a new tracker id with fresh skeletons
            c.append(cand(9, xa + sep0 * H, 300.0, k - 50))
        outs = sl.update(c, t, bounds=BOUNDS)
        for e in sl.events: print(f"    +{t - t0:.2f} s {e}")
        if 2 not in ids(outs) and gone is None: gone = t - t0
    print(f"    id 2 left the output after {gone:.2f} s; 1.5 s after B's new track 9 appeared at {sep0} h: emitted {ids(outs)}")

print("--- B'. both still 0.2 h apart; BOTH tracks stop being reported in the same frame")
sl = mk(); t, outs, xa = warm(sl, end_sep=0.2)
t0 = t
for k in range(1, 4):
    t += DT
    outs = sl.update([], t, bounds=BOUNDS)
    for e in sl.events: print(f"    +{t - t0:.2f} s {e}")
    print(f"    +{t - t0:.2f} s emitted {ids(outs)}")
