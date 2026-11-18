import cProfile
import pstats
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
t0 = time.time()
import chain_dusk as c  # noqa: E402

print("import", round(time.time() - t0, 1), flush=True)
sc = c.Scene(seed=7, day_frac=c.DAY_FRAC)
plate = c.CleanPlate.from_frames([sc.frame() for _ in range(40)], source="x")
print("plate", round(time.time() - t0, 1), flush=True)
c.RUN_S = 5.0
pr = cProfile.Profile()
pr.enable()
proc, rec, valid, stale = c.run(sc, plate, None, 1.0, "perfect")
pr.disable()
print("run 5s", round(time.time() - t0, 1), flush=True)
pstats.Stats(pr).sort_stats("cumulative").print_stats(25)
print(c.score(rec))
