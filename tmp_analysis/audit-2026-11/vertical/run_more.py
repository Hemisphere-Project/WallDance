#!/usr/bin/env python3
"""Pauses at the top border, exit after a still hang, re-entries, a neighbour at the entry point, portrait."""
import sys
import io
import time
import contextlib
from vsim import *

sc = Scene()
rx, ry, rw, rh = sc.roi
H = sc.H
V = 0.3 * H
cx_mid = rx + rw / 2
SEL = sys.argv[1:] or ["E", "F", "G", "H", "I"]


def quiet_run(*a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return run(*a, **k)


def seg_states(rows, t0, t1):
    """emitted-state histogram + number of frames with no OSC point in [t0, t1)"""
    h, n = {}, 0
    for r in rows:
        if t0 <= r["t"] < t1:
            n += 1
            k = r["out"][0]["st"] if r["out"] else "NONE"
            h[k] = h.get(k, 0) + 1
    return n, h


# ------------------------------------------------------------------ E: pause during a top entry
if "E" in SEL:
    print("\n===== E. top entry at 0.3 h/s that PAUSES 10 s with part of the body above the ROI, then goes on =====")

    def paused_entry(frac):
        t_pause = frac * H / V                       # s to have `frac` of the body inside

        def pos(t):
            if t < t_pause:
                return (cx_mid, ry - H + V * t)
            if t < t_pause + 10.0:
                return (cx_mid, ry - H + V * t_pause)
            return (cx_mid, min(ry - H + V * (t - 10.0), ry + 1.5 * H))
        return pos, t_pause

    for frac in (0.40, 0.60, 1.02):
        for motion, plate in ((True, True), (False, False)):
            pos, tp = paused_entry(frac)
            t0 = time.time()
            rows, proc = quiet_run(sc, [Body(pos)], int((tp + 10 + 5) * FPS), motion=motion, plate=plate)
            n, h = seg_states(rows, tp, tp + 10.0)
            n2, h2 = seg_states(rows, tp + 10.0, tp + 15.0)
            fe = first(rows, lambda r: len(r["out"]) > 0)
            xv = {}
            for r in rows:
                if tp <= r["t"] < tp + 10:
                    for k, v in r["xval"].items():
                        xv[k] = xv.get(k, 0) + v
            trk = sorted({(t["id"]) for r in rows for t in r["tracks"]})
            stat = any(s["static"] for r in rows for s in r["slots"])
            spots = sorted({sp for r in rows for sp in r["spots"]})
            print(f"[pause at {frac:.0%} visible | motion {'ON' if motion else 'OFF'} plate {'ON' if plate else 'OFF'}] "
                  f"pause {tp:.2f}-{tp + 10:.2f}s: OSC states over {n} frames {h}; crossval during pause {xv}; "
                  f"first OSC t={None if fe is None else round(fe['t'], 2)}s; after the pause (5 s) {h2}; "
                  f"tracker ids {trk}; slot ever static={stat} spots={spots}  ({time.time() - t0:.0f}s)", flush=True)
            if "-v" in sys.argv:
                timeline(rows, every=20)

# ------------------------------------------------------------------ F: still hang near the top, then exit upward
if "F" in SEL:
    print("\n===== F. dancer hangs STILL 12 s just under the top border (fully visible), then leaves upward at 0.3 h/s =====")
    TH = 12.0
    y_hang = ry + 0.15 * H

    def pos(t):
        if t < 3.0:                       # arrives from mid wall (moves: reputation)
            return (cx_mid, ry + 1.5 * H - (1.5 * H - 0.15 * H) * t / 3.0)
        if t < 3.0 + TH:
            return (cx_mid, y_hang)
        return (cx_mid, y_hang - V * (t - 3.0 - TH))

    for motion, plate in ((True, True), (True, False), (False, False)):
        t0 = time.time()
        rows, proc = quiet_run(sc, [Body(pos)], int((3 + TH + 14) * FPS), motion=motion, plate=plate)
        n, h = seg_states(rows, 3.0, 3.0 + TH)
        print(f"[motion {'ON' if motion else 'OFF'} plate {'ON' if plate else 'OFF'}] hang: OSC states over {n} frames {h}  ({time.time() - t0:.0f}s)")
        exit_report(sc, rows, label="exit after hang")
        prev = None
        for r in rows:
            if r["slots"] and r["slots"][0]["st"] == "coasting" and (prev is None or prev["slots"][0]["st"] != "coasting"):
                s = r["slots"][0]
                print(f"       coast starts t={r['t']:.2f}s hold_s={s['hold']} rep={s['rep']} travel={s['travel']}h vel={s['vel']} static={s['static']}")
            prev = r
        print("       events:", [(round(r["t"], 2), e["ev"], e.get("why")) for r in rows for e in r["ev"]], flush=True)
        if "-v" in sys.argv:
            timeline([r for r in rows if r["t"] > 3 + TH - 1], every=10, upto=3 + TH + 12)

# ------------------------------------------------------------------ G: exit by the top, re-enter by the top
if "G" in SEL:
    print("\n===== G. top exit (1.0 h/s) then a top RE-ENTRY at 0.3 h/s after a gap, same x or 2 h aside =====")
    T_EXIT = 4.0
    y_wall = ry + 1.0 * H
    t_out = T_EXIT + (y_wall + H - ry) / (1.0 * H)       # fully out

    for gap, dx in ((2.0, 0.0), (5.0, 0.0), (12.0, 0.0), (2.0, 2.0), (5.0, 2.0), (16.0, 2.0)):
        def pos(t, gap=gap, dx=dx):
            if t < T_EXIT:
                return (cx_mid + 0.3 * H * np.sin(2 * np.pi * t / 4.0), y_wall)
            if t < t_out:
                return (cx_mid, y_wall - 1.0 * H * (t - T_EXIT))
            if t < t_out + gap:
                return None
            return (cx_mid + dx * H, min(ry - H + V * (t - t_out - gap), ry + 1.5 * H))
        t0 = time.time()
        rows, proc = quiet_run(sc, [Body(pos)], int((t_out + gap + 7) * FPS), motion=True, plate=True)
        t_in = t_out + gap
        sub = [r for r in rows if r["t"] >= t_in]
        fd = first(sub, lambda r: r["n_raw"] > 0)
        fe = first(sub, lambda r: len(r["out"]) > 0)
        ghost = [r for r in rows if t_out + 0.05 < r["t"] < t_in and r["out"]]
        ev = [(round(r["t"], 2), e["ev"], e.get("key"), e.get("prev")) for r in sub for e in r["ev"]]
        tids = sorted({t["id"] for r in sub for t in r["tracks"] if t["tsu"] == 0})
        sids = sorted({o["id"] for r in sub for o in r["out"]})
        print(f"[gap {gap:>4.1f}s dx {dx:.0f}h] empty-wall frames with an OSC point: {len(ghost)} ({len(ghost) / FPS:.2f}s) | "
              f"re-entry: first det t=+{fd['t'] - t_in:.2f}s, OSC +{None if fe is None else round(fe['t'] - t_in, 2)}s "
              f"(vis {None if fe is None else fe['vis'][0]}) | tracker ids fed after re-entry {tids} | slot ids {sids} | events {ev}  ({time.time() - t0:.0f}s)", flush=True)

# ------------------------------------------------------------------ H: a dancer already at the entry point
if "H" in SEL:
    print("\n===== H. dancer B on the wall near the border; dancer A enters at 0.3 h/s next to B (max_dancers=2) =====")

    def run_pair(kind, d):
        if kind == "top":
            bpos = lambda t: (cx_mid + 0.05 * H * np.sin(2 * np.pi * t / 3.0), ry + 0.10 * H)
            apos = lambda t: None if t < 5.0 else (cx_mid + d * H, min(ry - H + V * (t - 5.0), ry + 0.10 * H))
        else:  # side: B near the left border at mid height, A enters from the left d h lower
            bx = rx + 0.25 * H
            bpos = lambda t: (bx + 0.05 * H * np.sin(2 * np.pi * t / 3.0), ry + 1.5 * H)
            apos = lambda t: None if t < 5.0 else (min(rx - BOX_W * H / 2 + V * (t - 5.0), bx), ry + 1.5 * H + d * H)
        return quiet_run(sc, [Body(bpos, name="B"), Body(apos, name="A")], int(14 * FPS), motion=True, plate=True)

    for kind, d in (("top", 0.5), ("top", 0.7), ("top", 0.9), ("top", 1.2), ("side", 0.5), ("side", 0.7), ("side", 0.9)):
        t0 = time.time()
        rows, proc = run_pair(kind, d)
        sub = [r for r in rows if r["t"] >= 5.0]
        fd = first(sub, lambda r: r["n_raw"] >= 2)
        f2 = first(sub, lambda r: len(r["out"]) >= 2)
        ft2 = first(sub, lambda r: sum(1 for t in r["tracks"] if t["tsu"] == 0) >= 2)
        n2 = sum(1 for r in sub if len(r["out"]) >= 2)
        nA = sum(1 for r in sub if (r["vis"][1] or 0) >= 0.999)
        # B's emitted point: largest frame-to-frame jump while A enters (keypoint splice / forced updates)
        bjump, prevo = 0.0, None
        for r in sub:
            o = next((o for o in r["out"] if o["id"] == 1), None)
            if o is not None and prevo is not None:
                bjump = max(bjump, float(np.hypot(o["x"] - prevo["x"], o["y"] - prevo["y"])) / H)
            prevo = o
        import collections
        logev = collections.Counter()
        try:
            import glob
            for fn in glob.glob(proc.tracker.logger._session_dir + "/tracking_events*.jsonl"):
                for line in open(fn):
                    e = json.loads(line)
                    if e.get("event") in ("NEW_TRACK", "FORCE_UPDATE", "FALLBACK_UPDATE", "DUPLICATE_IGNORED", "AMBIGUOUS_IGNORED",
                                          "RESURRECT", "ANTI_MERGE", "DORMANT") and e.get("frame", 0) >= 80 + 100:
                        logev[e["event"]] += 1
        except Exception as exc:
            logev["log-read-failed:" + type(exc).__name__] += 1
        print(f"[{kind} entry, A {d:.1f} h from B's axis] first frame with 2 YOLO dets t={None if fd is None else round(fd['t'] - 5, 2)}s "
              f"(A vis {None if fd is None else fd['vis'][1]}) | 2 fed tracks from +{None if ft2 is None else round(ft2['t'] - 5, 2)}s | "
              f"2 OSC points from +{None if f2 is None else round(f2['t'] - 5, 2)}s (A vis {None if f2 is None else f2['vis'][1]}) | "
              f"frames with 2 points {n2} of {nA} with A fully in | B's max point jump {bjump:.2f} h/frame | tracker events {dict(logev)}  ({time.time() - t0:.0f}s)", flush=True)

# ------------------------------------------------------------------ I: portrait
if "I" in SEL:
    print("\n===== I. portrait (camera rotated 90, Rotate applied): frame 1304x1776, ROI 1000x1500, H=105 px (30 m) =====")
    sp = Scene(frame_w=1304, frame_h=1776, roi=(152, 100, 1000, 1500), H=105.0)
    px, py, pw, ph = sp.roi
    print("letterbox scale %.4f pad_x %d pad_y %d | tracker content x [%d, %d] of %d; edge zone %.0f lb px = %.2f h per side | "
          "the tracker is told pad_x only (pipeline.py:984-985)" % (*sp.lb, sp.lb[1], sp.imgsz - sp.lb[1], sp.imgsz,
                                                                    0.12 * pw * sp.lb[0], 0.12 * pw / sp.H))
    Vp = 0.3 * sp.H
    for label, pos in (
        ("top 0.3h/s", lambda t: (px + pw / 2, min(py - sp.H + Vp * t, py + 2 * sp.H))),
        ("side 0.3h/s", lambda t: (min(px - BOX_W * sp.H / 2 + Vp * t, px + 3 * sp.H), py + 6 * sp.H)),
    ):
        rows, proc = quiet_run(sp, [Body(pos)], 240, motion=True, plate=True)
        entry_report(sp, rows, label="portrait " + label)
    T0 = 4.0
    for label, pos in (
        ("top exit 0.3h/s", lambda t: (px + pw / 2, py + 1.5 * sp.H) if t < T0 else (px + pw / 2, py + 1.5 * sp.H - Vp * (t - T0))),
        ("side exit 0.3h/s", lambda t: (px + 2 * sp.H + 0.3 * sp.H * np.sin(t), py + 6 * sp.H) if t < T0
         else (px + 2 * sp.H + 0.3 * sp.H * np.sin(T0) - Vp * (t - T0), py + 6 * sp.H)),
    ):
        rows, proc = quiet_run(sp, [Body(pos)], 520, motion=True, plate=True)
        exit_report(sp, rows, label="portrait " + label)
