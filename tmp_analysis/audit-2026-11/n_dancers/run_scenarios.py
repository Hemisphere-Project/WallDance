"""Regime n-greater-than-2: logic-path scenarios through the shipped chain (see harness.py)."""
import json
import sys

import numpy as np

from harness import (FPS, PH, Health, Scene, emitted_rows, make_det, make_proc, score, step)


def sway(x0, y0, h, ax=0.4, ay=0.15, period_s=5.0, phase=0.0, start=0, end=None):
    def f(i):
        if i < start or (end is not None and i >= end):
            return None
        a = 2 * np.pi * (i / FPS) / period_s + phase
        return (x0 + ax * h * np.sin(a), y0 + ay * h * np.sin(2 * a), h)
    return f


def still(x0, y0, h, start=0, end=None):
    def f(i):
        if i < start or (end is not None and i >= end):
            return None
        return (x0, y0, h)
    return f


def path(points, h):
    """points: [(frame, x, y)] piecewise linear; absent outside the first/last frame."""
    fr = [p[0] for p in points]
    xs = [p[1] for p in points]
    ys = [p[2] for p in points]

    def f(i):
        if i < fr[0] or i > fr[-1]:
            return None
        return (float(np.interp(i, fr, xs)), float(np.interp(i, fr, ys)), h)
    return f


def chain(*fs):
    def f(i):
        for g in fs:
            r = g(i)
            if r is not None:
                return r
        return None
    return f


class Markov:
    """Bursty YOLO misses: duty = share of detected frames, mean miss burst in frames."""

    def __init__(self, duty=1.0, burst=3.0, seed=1, forced=None):
        self.rng = np.random.default_rng(seed)
        self.duty, self.burst = duty, burst
        self.state = {}
        self.forced = forced or {}          # name -> set of frames forced missed

    def detected(self, name, i):
        if i in self.forced.get(name, ()):
            return False
        if self.duty >= 1.0:
            return True
        miss = self.state.get(name, False)
        p_leave = 1.0 / self.burst
        p_enter = p_leave * (1 - self.duty) / self.duty
        miss = (self.rng.random() > p_leave) if miss else (self.rng.random() < p_enter)
        self.state[name] = miss
        return not miss


WANT = None


def run(name, proc_factory, dancers, n_frames, det=None, bodies_in_gray=True, static_figures=(),
        extra=None, det_kw=None, order=None, seed=0, verbose_events=True, note=""):
    if WANT is not None and not any(name.startswith(w) for w in WANT):
        return None
    proc = proc_factory()
    det = det or Markov()
    det_kw = det_kw or {}
    rng = np.random.default_rng(seed)
    scene = Scene(seed, static_figures=static_figures)
    health = Health()
    gts, outs = [], []
    events = []
    n_rep, n_int, kept_hist, over_cap = [], [], [], 0
    for i in range(n_frames):
        g = {n: f(i) for n, f in dancers.items() if f(i) is not None}
        dets = []
        names = list(g) if order is None else order(i, list(g), rng)
        for n in names:
            x, y, h = g[n]
            if det.detected(n, i):
                dets.append(make_det(x, y, h, rng, **det_kw.get(n, {})))
        if extra is not None:
            dets += extra(i, rng)
        gray = scene.render(list(g.values()) if bodies_in_gray else [])
        tracks, emitted, tm = step(proc, dets, gray, i)
        health.tick(proc, tracks, i)
        n_rep.append(len(tracks))
        n_int.append(len(proc.tracker.tracks))
        kept_hist.append((tm["n_det_in"], tm["n_det_kept"]))
        over_cap += 1 if proc.tracker.last_over_cap else 0
        for ev in (proc._slots.events if proc._slots is not None else []):
            events.append((i, ev))
        gts.append(g)
        outs.append(emitted_rows(emitted))
    res = score(gts, outs)
    res["name"] = name
    res["note"] = note
    res["max_dancers"] = proc.settings.max_dancers
    res["max_persons"] = proc.tracker.max_persons
    res["tracker_reported_mean"] = round(float(np.mean(n_rep)), 2)
    res["tracker_internal_mean"] = round(float(np.mean(n_int)), 2)
    res["dets_in_vs_kept"] = (int(sum(a for a, _ in kept_hist)), int(sum(b for _, b in kept_hist)))
    res["over_cap_frames"] = over_cap
    res["alerts"] = health.alerts
    res["slot_counters"] = dict(proc._slots.counters) if proc._slots is not None else {}
    res["final_states"] = proc.slot_states()
    print("=" * 100)
    print(f"{name}   cap max_dancers={res['max_dancers']} max_persons={res['max_persons']}   {note}")
    print(f"  frames {res['frames']}  points/frame histogram {res['points_per_frame']}")
    print(f"  ghost frames {res['ghost_frames']} (ghost points {res['ghost_points']})   slot->other-dancer moves {res['slot_moves']}")
    print(f"  tracker: reported mean {res['tracker_reported_mean']}, internal mean {res['tracker_internal_mean']}, "
          f"dets in/kept by dedupe {res['dets_in_vs_kept']}, over-cap frames {over_cap}")
    for n, d in res["per_dancer"].items():
        print(f"    {n}: present {d['present_s']}s coverage {d['coverage']:.3f} longest hole {d['longest_hole_s']}s "
              f"first point after {d['first_point_after_s']}s slot ids {d['slot_ids']}")
    print(f"  alerts: {res['alerts'] or 'none'}")
    print(f"  slot counters: {res['slot_counters']}")
    print(f"  final slot states: {res['final_states']}")
    if verbose_events:
        shown = [(i, e) for i, e in events if e["ev"] != "lost" or e.get("why") != "coast_expired"][:14]
        print("  slot events (first 14, coast_expired omitted): " +
              "; ".join(f"f{i}:{e['ev']} s{e['slot']}" + (f"<-T{e['key']}" if e.get('key') is not None else "")
                        + (f"({e['why']})" if e.get('why') else "") for i, e in shown))
    return res


def main():
    h = PH
    out = []

    # ---------------- A: cap 2 (shipped default), three dancers ----------------
    d = {"a": chain(sway(300, 600, h, end=400), path([(400, 300, 600), (460, 8, 600)], h)),
         "b": sway(640, 600, h, phase=1.0, start=60),
         "c": sway(980, 600, h, phase=2.0, start=120)}
    out.append(run("A1 cap2 / 3 dancers staggered, a exits left at 20-23 s", (lambda: make_proc()), d, 700,
                   note="perfect detection, bodies in gray"))

    d = {"a": sway(300, 600, h), "b": sway(640, 600, h, phase=1.0), "c": sway(980, 600, h, phase=2.0)}
    out.append(run("A0 cap2 / 3 dancers present from frame 0 (det order a,b,c)", (lambda: make_proc()), d, 300))
    out.append(run("A0' same, det order c,b,a", (lambda: make_proc()), d, 300, order=lambda i, n, r: list(reversed(n))))

    # slot hopping: b (slotted) misses YOLO for 0.5 s every 5 s; c (the spare dancer) is 1.2 h away
    d = {"a": sway(300, 600, h, ax=0.1), "b": sway(640, 600, h, ax=0.1, phase=1.0),
         "c": sway(640 + 1.2 * h, 600, h, ax=0.1, phase=1.0, start=100)}
    forced = {"b": {f for k in range(2, 6) for f in range(k * 100, k * 100 + 10)}}
    out.append(run("A2 cap2 / 3 dancers, slotted b missed 0.5 s every 5 s, spare c 1.2 h away (no motion evidence)",
                   (lambda: make_proc()), d, 620, det=Markov(forced=forced), bodies_in_gray=False,
                   note="dark body on dark wall: no MOG2 / frame-diff"))
    out.append(run("A2' same with motion evidence (bridge holds b)", (lambda: make_proc()), d, 620,
                   det=Markov(forced=forced), bodies_in_gray=True))

    # static yield: the still slot-holder gives its id to the 3rd (moving) dancer
    d = {"a": sway(300, 600, h), "b": still(640, 600, h),
         "c": chain(path([(200, 1270, 900), (260, 1000, 900)], h), sway(1000, 900, h, start=261))}
    out.append(run("A3 cap2 / a moves, b hangs still, c (3rd) walks in at 10 s", (lambda: make_proc()), d, 900,
                   det_kw={"b": {"jitter": 0.004}}))

    # ---------------- B: cap 4, three dancers, one spare slot ----------------
    ghost_xy = (1100, 300)
    fig = [(ghost_xy[0], ghost_xy[1], h)]

    def ghost_every(i, rng):
        return [make_det(ghost_xy[0], ghost_xy[1], h, rng, conf=0.6, n_visible=12, jitter=0.003)]

    def ghost_bursty(i, rng):
        return ghost_every(i, rng) if (i % 20) < 12 else []

    d3 = {"a": sway(250, 650, h), "b": sway(550, 650, h, phase=1.0), "c": sway(850, 650, h, phase=2.0)}
    d4 = dict(d3)
    d4["d"] = chain(path([(600, 700, 1275), (660, 700, 1000)], h), sway(700, 1000, h, start=661))
    out.append(run("B0 cap4 / 3 dancers, no ghost (control)", (lambda: make_proc(max_dancers=4)), d3, 600))
    out.append(run("B1 cap4 / 3 dancers + static YOLO figure every frame; 4th dancer enters at 30 s",
                   (lambda: make_proc(max_dancers=4)), d4, 1000, static_figures=fig, extra=ghost_every))
    out.append(run("B2 cap4 / 3 dancers + static YOLO figure 12 on / 8 off; 4th dancer enters at 30 s",
                   (lambda: make_proc(max_dancers=4)), d4, 1000, static_figures=fig, extra=ghost_bursty))
    out.append(run("B3 cap2 / 2 dancers + the same static figure (the validated N=2 regime, control)",
                   (lambda: make_proc()), {"a": d3["a"], "b": d3["b"]}, 600, static_figures=fig, extra=ghost_every))

    # ---------------- C: cap 4, four dancers ----------------
    d = {"a": sway(250, 500, h), "b": sway(560, 520, h, phase=1.0),
         "c": sway(870, 500, h, phase=2.0),
         "d": chain(sway(1100, 800, h, phase=0.5, end=400),
                    path([(400, 1100, 800), (470, 1100, 1275)], h),          # leaves by the floor
                    path([(700, 400, 5), (770, 400, 250)], h),                # comes back from the roof
                    sway(400, 250, h, start=771))}
    out.append(run("C1 cap4 / 4 dancers, perfect detection, d exits bottom 20-23.5 s, re-enters top at 35 s",
                   (lambda: make_proc(max_dancers=4)), d, 1200))
    out.append(run("C1' same, bursty YOLO misses (duty 0.75, mean burst 3 frames), motion evidence",
                   (lambda: make_proc(max_dancers=4)), d, 1200, det=Markov(0.75, 3.0, seed=3)))
    out.append(run("C1'' same, bursty misses (duty 0.75), NO motion evidence",
                   (lambda: make_proc(max_dancers=4)), d, 1200, det=Markov(0.75, 3.0, seed=3), bodies_in_gray=False))
    out.append(run("C1''' same, duty 0.5 mean burst 4, motion evidence",
                   (lambda: make_proc(max_dancers=4)), d, 1200, det=Markov(0.5, 4.0, seed=3)))

    # all four slots full, one dancer hangs still, a 5th moving body (a rigger, a shadow) shows up
    d = {"a": sway(250, 500, h), "b": sway(560, 520, h, phase=1.0), "c": sway(870, 500, h, phase=2.0),
         "d": still(1100, 800, h),
         "e": chain(path([(300, 5, 1150), (420, 400, 1150)], h), sway(400, 1150, h, start=421, end=700))}
    out.append(run("C2 cap4 / 4 dancers (d hangs still), a 5th moving body e on stage 15-35 s",
                   (lambda: make_proc(max_dancers=4)), d, 1100, det_kw={"d": {"jitter": 0.004}}))

    # ---------------- D: one global person height, dancers of different apparent size ----------------
    d = {"a": sway(300, 1000, 150, ax=0.2),
         "b": sway(700, 350, 90, ax=0.05),
         "c": chain(still(700 + 1.2 * 90, 350, 90, end=300), path([(300, 808, 350), (340, 700 + 2.0 * 90, 350)], 90),
                    sway(700 + 2.0 * 90, 350, 90, ax=0.05, start=341))}
    out.append(run("D1 cap4, person_height 150 / a=150 px, b,c=90 px side by side 1.2 own-heights apart until 15 s",
                   (lambda: make_proc(max_dancers=4, person_height=150)), d, 600))
    out.append(run("D1' same scene with person_height 90",
                   (lambda: make_proc(max_dancers=4, person_height=90)), d, 600))
    d = {"b": sway(700, 600, h, ax=0.05), "c": chain(still(700 + 0.7 * h, 600, h, end=300),
                                                    path([(300, 700 + 0.7 * h, 600), (340, 700 + 1.5 * h, 600)], h),
                                                    sway(700 + 1.5 * h, 600, h, ax=0.05, start=341))}
    out.append(run("D2 cap4, equal heights: c appears 0.7 h beside tracked b (a duo entering together), parts at 15 s",
                   (lambda: make_proc(max_dancers=4)), d, 600))

    # ---------------- E: pre-tracker shadow suppression between neighbours ----------------
    d = {"a": sway(500, 600, h, ax=0.1), "b": sway(500 + 1.2 * h, 600, h, ax=0.1, phase=1.0)}
    out.append(run("E1 cap4 / a well seen, b dim (7 keypoints over 0.3) 1.2 h beside a", (lambda: make_proc(max_dancers=4)), d, 400,
                   det_kw={"b": {"n_visible": 7}}))
    out.append(run("E1' same, b dim by confidence (17 kpts, mean 0.42)", (lambda: make_proc(max_dancers=4)), d, 400,
                   det_kw={"b": {"conf": 0.42}}))
    out.append(run("E2 control: the dim dancer b alone", (lambda: make_proc(max_dancers=4)), {"b": d["b"]}, 400,
                   det_kw={"b": {"n_visible": 7}}))
    d3 = {"a": sway(500, 600, h, ax=0.1), "b": sway(500 + 2.0 * h, 600, h, ax=0.1, phase=1.0)}
    out.append(run("E3 control: same dim b but 2.0 h from a", (lambda: make_proc(max_dancers=4)), d3, 400,
                   det_kw={"b": {"n_visible": 7}}))

    # ---------------- G: the report cap max_persons = 6 ----------------
    figs = [(200, 1100, h), (640, 1150, h), (1080, 1100, h)]

    def three_ghosts(i, rng):
        return [make_det(x, y, hh, rng, conf=0.6, n_visible=12, jitter=0.003) for x, y, hh in figs]
    d = {"a": sway(250, 500, h), "b": sway(560, 520, h, phase=1.0), "c": sway(870, 500, h, phase=2.0),
         "d": chain(path([(300, 1275, 250), (360, 1100, 250)], h), sway(1100, 250, h, start=361))}
    out.append(run("G1 cap4, max_persons 6 / 3 dancers + 3 static YOLO figures, 4th dancer enters at 15 s",
                   (lambda: make_proc(max_dancers=4)), d, 900, static_figures=figs, extra=three_ghosts))

    json.dump([o for o in out if o], open(sys.argv[1], "w"), indent=1, default=str)


if __name__ == "__main__":
    if len(sys.argv) > 2:
        WANT = sys.argv[2].split(",")
    main()
