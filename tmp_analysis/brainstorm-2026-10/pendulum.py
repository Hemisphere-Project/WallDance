"""Gap injection on YOLO positions (conf >= 0.5, one dancer): predict the position k frames after the last
measurement with hold / shipped velocity decay (tau 0.25 s) / constant velocity / rope pendulum about a fixed anchor.
Anchor: median of good 1.5 s circle fits over the take (data-driven), or --anchor x,y.
usage: pendulum.py TIMELINE MANIFEST [--anchor x,y]"""
import os as _os_wd, sys as _sys_wd
os = _os_wd
_REPO = next((r for r in ('.', '..', '../..', '/data/WallDance') if os.path.isdir(os.path.join(r, 'application', 'tests'))), '..')
import sys, json, math, argparse
import numpy as np
sys.path.insert(0, os.path.join(_REPO, 'application', 'tests')); import scoring
ap = argparse.ArgumentParser(); ap.add_argument('timeline'); ap.add_argument('manifest'); ap.add_argument('--anchor', default=None)
a = ap.parse_args()
man = scoring.load_scenario(a.manifest); fps = man['fps']; H = float(man['config']['person_height_px'])
g_px = 9.81 * H / 1.7  # px/s^2 with a 1.7 m dancer of H px
P = {}; spots = man.get('reference', {}).get('exclude_spots', []); last = None
for r in scoring._load_timeline(a.timeline):
    refs = [q for q in r.get('ref') or [] if (q['conf'] or 1) >= 0.5 and not any(math.hypot(q['c'][0]-sx, q['c'][1]-sy) <= sr for sx, sy, sr in spots)]
    if not refs: continue
    q = max(refs, key=lambda q: q['conf'] or 1) if last is None else min(refs, key=lambda q: math.hypot(q['c'][0]-last[0], q['c'][1]-last[1]))
    if last is not None and math.hypot(q['c'][0]-last[0], q['c'][1]-last[1]) > 1.5 * H * max(1, (r['frame'] - last[2])) ** 0.5: continue
    P[r['frame']] = (q['c'][0], q['c'][1], q['h']); last = (q['c'][0], q['c'][1], r['frame'])
F = sorted(P)
def circle_fit(xy):
    x, y = xy[:, 0], xy[:, 1]; A = np.c_[2*x, 2*y, np.ones(len(x))]; b = x*x + y*y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None); cx, cy = sol[0], sol[1]; R = math.sqrt(max(sol[2] + cx*cx + cy*cy, 1e-6))
    res = np.sqrt((x-cx)**2 + (y-cy)**2) - R; return cx, cy, R, float(np.sqrt((res**2).mean()))
if a.anchor:
    AX, AY = [float(v) for v in a.anchor.split(',')]
else:
    cs = []
    for i in range(0, len(F) - 30, 10):
        win = [f for f in F[i:i+30] if f - F[i] < 30]
        if len(win) < 25: continue
        xy = np.array([P[f][:2] for f in win])
        if np.ptp(xy[:, 0]) < 0.8 * H: continue   # need a real swing to fit
        cx, cy, R, rms = circle_fit(xy)
        if rms < 0.05 * H and 2 * H < R < 30 * H and cy < xy[:, 1].min(): cs.append((cx, cy))
    cs = np.array(cs); AX, AY = np.median(cs[:, 0]), np.median(cs[:, 1])
    print('anchor from %d good circle fits: (%.0f, %.0f), IQR x %.0f y %.0f px' % (len(cs), AX, AY, *(np.percentile(cs, 75, 0) - np.percentile(cs, 25, 0))))
def predict(f0, k, kind):
    hist = [f for f in range(f0 - 4, f0 + 1) if f in P]
    xy = np.array([P[f][:2] for f in hist]); tt = np.array(hist) / fps
    p0 = np.array(P[f0][:2]); T = k / fps
    if kind == 'hold': return p0
    v = np.polyfit(tt, xy, 1)[0] if len(hist) >= 3 else np.zeros(2)
    if kind == 'decay': return p0 + v * 0.25 * (1 - math.exp(-T / 0.25))
    if kind == 'constvel': return p0 + v * T
    # pendulum about (AX, AY)
    th = np.arctan2(xy[:, 0] - AX, xy[:, 1] - AY); R = float(np.hypot(p0[0] - AX, p0[1] - AY))
    w0 = np.polyfit(tt, np.unwrap(th), 1)[0] if len(hist) >= 3 else 0.0
    t_, w_ = float(th[-1]), float(w0); dt = 0.005
    damp = 0.0 if kind == 'pendulum' else 0.3
    for _ in range(int(T / dt)):
        acc = -(g_px / R) * math.sin(t_) - damp * w_; w_ += acc * dt; t_ += w_ * dt
    pp = np.array([AX + R * math.sin(t_), AY + R * math.cos(t_)])
    if kind == 'pend_blend':   # trust the pendulum only as far as the shipped decay would move (robust variant)
        return 0.5 * pp + 0.5 * (p0 + v * 0.25 * (1 - math.exp(-T / 0.25)))
    return pp
kinds = ['hold', 'decay', 'constvel', 'pendulum', 'pend_damped', 'pend_blend']
res = {}
for k in (5, 10, 20, 40):
    errs = {kd: {'fast': [], 'slow': []} for kd in kinds}
    for f0 in F[::3]:
        if f0 + k not in P or sum(f in P for f in range(f0 - 4, f0 + 1)) < 4: continue
        hist = [f for f in range(f0 - 4, f0 + 1) if f in P]; xy = np.array([P[f][:2] for f in hist])
        spd = np.linalg.norm(np.polyfit(np.array(hist) / fps, xy, 1)[0]) / H
        cls = 'fast' if spd > 0.5 else 'slow'; truth = np.array(P[f0 + k][:2])
        for kd in kinds: errs[kd][cls].append(np.linalg.norm(predict(f0, k, kd) - truth) / H)
    res[k] = {kd: {c: (round(float(np.median(v)), 3), round(float(np.percentile(v, 90)), 3), len(v)) for c, v in d.items() if v} for kd, d in errs.items()}
print('error vs YOLO position at t0+k, median / p90 in dancer heights (n)')
for k, d in res.items():
    print(f'k={k} ({k/fps:.2f} s)')
    for kd, cd in d.items(): print('   %-12s' % kd, '  '.join(f'{c}: {m:.3f}/{p:.3f} (n={n})' for c, (m, p, n) in cd.items()))
