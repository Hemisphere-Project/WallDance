"""Why does the hanging dancer lose its point when the plate is stale?  Slot events of one run
(plate as captured at 0.65x, perfect YOLO stand-in, 60 s)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chain_dusk as c  # noqa: E402
from core import identity_slots as ids  # noqa: E402

log = []
orig = ids.IdentitySlots._event


def _event(self, kind, sid, **kw):
    log.append((round(float(self._t or 0.0), 2), kind, sid, {k: (round(v, 2) if isinstance(v, float) else v)
                                                              for k, v in kw.items()}))
    return orig(self, kind, sid, **kw)


ids.IdentitySlots._event = _event
c.RUN_S = 60.0
sc = c.Scene(seed=7, day_frac=c.DAY_FRAC)
plate = c.CleanPlate.from_frames([sc.frame() for _ in range(40)], source="synthetic calibrate")
proc, rec, valid, stale = c.run(sc, plate, None, 0.65, "perfect")
print("plate valid frames", valid, "stale", stale)
for row in log:
    print(row)
print(c.score(rec))
print({k: v for k, v in proc._slots.counters.items() if v})
