"""Extra N>2 probes: synchronous entry of two dancers side by side (shadow-track kill), and the
hold after an exit with three dancers and two slots."""
import sys

import numpy as np

from harness import PH, make_proc
from run_scenarios import Markov, chain, path, run, still, sway

h = PH
which = sys.argv[1] if len(sys.argv) > 1 else "S"

if "S" in which:
    # two dancers come down from the roof side by side (1.2 h apart), 90 px/s for 6 s, then sway.
    # a third dancer is already on the wall.
    for cb, label in ((0.80, "equal brightness"), (0.62, "p2 dimmer (keypoint conf 0.62 vs 0.80)")):
        d = {"a": sway(250, 700, h),
             "p1": chain(path([(100, 700, 70), (220, 700, 610)], h), sway(700, 610, h, ax=0.1, start=221)),
             "p2": chain(path([(100, 700 + 1.2 * h, 70), (220, 700 + 1.2 * h, 610)], h),
                         sway(700 + 1.2 * h, 610, h, ax=0.1, start=221))}
        run(f"S1 cap4 / p1,p2 descend together 1.2 h apart from 5 s to 11 s, {label}",
            lambda: make_proc(max_dancers=4), d, 420, det_kw={"p2": {"conf": cb}})
    d = {"a": sway(250, 700, h),
         "p1": chain(path([(100, 700, 70), (220, 700, 610)], h), sway(700, 610, h, ax=0.1, start=221)),
         "p2": chain(path([(100, 700 + 2.0 * h, 70), (220, 700 + 2.0 * h, 610)], h),
                     sway(700 + 2.0 * h, 610, h, ax=0.1, start=221))}
    run("S2 control: same, 2.0 h apart, p2 dimmer", lambda: make_proc(max_dancers=4), d, 420,
        det_kw={"p2": {"conf": 0.62}})
