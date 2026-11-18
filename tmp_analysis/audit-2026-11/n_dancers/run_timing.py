"""Per-frame cost of the shipped post-YOLO chain vs N (CPU only, synthetic).

Timers: THREAD CPU time (time.thread_time) of the main thread per stage -- the dev box was shared
with other jobs during the audit, so wall time is not usable.  dev37 = i7-3770K (4C/8T), about
2.5x slower per thread than the show laptop (CONTEXT §5).
"""
import cProfile
import io
import pstats
import sys
import time

import numpy as np

from harness import FPS, PH, Scene, make_det, make_proc, step
from run_scenarios import Markov, sway


def layout(n, mode):
    h = PH
    if mode == "spread":           # >= 2.5 h apart
        pts = [(250, 450), (640, 450), (1030, 450), (250, 900), (640, 900), (1030, 900)]
    else:                          # "cluster": 1.0 h apart (a group figure)
        pts = [(520 + 1.0 * h * (k % 3), 560 + 1.0 * h * (k // 3)) for k in range(6)]
    return {f"d{k}": sway(x, y, h, ax=0.3, phase=0.7 * k) for k, (x, y) in enumerate(pts[:n])}


def cpu_wrap(obj, name, store, key):
    orig = getattr(obj, name)

    def w(*a, **k):
        t0 = time.thread_time()
        try:
            return orig(*a, **k)
        finally:
            store[key] = store.get(key, 0.0) + (time.thread_time() - t0) * 1000
    setattr(obj, name, w)


def run(n, mode, duty, frames=330, skip=80, profile=False, bodies=True, max_dancers=None, quiet=False):
    proc = make_proc(max_dancers=n if max_dancers is None else max_dancers)
    acc = {}
    cpu_wrap(proc.tracker, "update", acc, "tracker")
    cpu_wrap(proc.tracker, "_run_matching_phase", acc, "match")
    cpu_wrap(proc.tracker, "_lazy_bridge_with_motion", acc, "bridge")
    cpu_wrap(proc.tracker, "_finalize_track_lifecycle", acc, "lifecycle")
    cpu_wrap(proc.tracker, "_log_frame_summary", acc, "log")
    cpu_wrap(proc, "_run_identity_slots", acc, "slots")
    cpu_wrap(proc, "_post_yolo_chain", acc, "chain")
    cpu_wrap(proc, "_filter_duplicate_detections", acc, "dedupe")
    cpu_wrap(proc, "_crossval_motion_filter", acc, "crossval")
    cpu_wrap(proc, "_emit", acc, "emit")
    dancers = layout(n, mode)
    det = Markov(duty, 3.0, seed=7)
    rng = np.random.default_rng(5)
    scene = Scene(5)
    rows = []
    keys = ["dedupe", "crossval", "tracker", "match", "bridge", "lifecycle", "log", "slots", "emit", "chain"]
    pr = cProfile.Profile() if profile else None
    for i in range(frames):
        g = {k: f(i) for k, f in dancers.items()}
        dets = [make_det(*g[k], rng) for k in g if det.detected(k, i)]
        gray = scene.render(list(g.values()) if bodies else [])
        acc.clear()
        if pr is not None and i >= skip:
            pr.enable()
        tracks, emitted, tm = step(proc, dets, gray, i)
        if pr is not None and i >= skip:
            pr.disable()
        if i >= skip:
            rows.append([acc.get(k, 0.0) for k in keys]
                        + [len(proc.tracker.tracks), len(tracks), len(emitted), tm["dgrams"]])
    a = np.array(rows, float)
    m = {k: a[:, j].mean() for j, k in enumerate(keys)}
    p95 = {k: np.percentile(a[:, j], 95) for j, k in enumerate(keys)}
    if not quiet:
        print(f"N={n} {mode:7s} duty={duty:.2f} | dedupe {m['dedupe']:5.2f} | crossval {m['crossval']:5.2f} "
              f"| tracker.update {m['tracker']:6.2f} (p95 {p95['tracker']:6.2f}; match {m['match']:5.2f} bridge {m['bridge']:5.2f} "
              f"lifecycle {m['lifecycle']:5.2f} log {m['log']:5.2f}) | slots {m['slots']:5.2f} (p95 {p95['slots']:5.2f}) "
              f"| OSC emit {m['emit']:5.2f} | whole chain {m['chain']:6.2f} (p95 {p95['chain']:6.2f}) "
              f"| tracks int {a[:, 10].mean():.1f} rep {a[:, 11].mean():.1f} emitted {a[:, 12].mean():.2f} dgrams {a[:, 13].mean():.1f}")
    if pr is not None:
        s = io.StringIO()
        pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(22)
        print(s.getvalue()[:6000])
    return m


if __name__ == "__main__":
    print("thread-CPU ms per frame (mean), dev37 i7-3770K, frames 80..329 at 20 fps; chain = _post_yolo_chain "
          "(crossval + exclusion + cold blobs + tracker + finalize + slots + OSC)")
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "table"):
        for mode, duty in (("spread", 1.0), ("spread", 0.7), ("cluster", 0.7)):
            for n in range(1, 7):
                run(n, mode, duty)
    if which in ("all", "profile"):
        print("\n--- cProfile (tottime), N=4 cluster duty 0.7 ---")
        run(4, "cluster", 0.7, profile=True, quiet=True)
