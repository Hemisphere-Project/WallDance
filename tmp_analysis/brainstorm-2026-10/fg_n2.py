"""Known-N foreground-mass points from a clean plate, scored against the YOLO pseudo-GT of a replay timeline.
usage: fg_n2.py STACK.npy TIMELINE.json MANIFEST.json OUT.json [--k 4] [--bg global|causal|first] [--core 0]
Per frame: |I - B| > k*sigma -> open/close -> CC (area >= min) -> exactly N points: the N heaviest components;
if fewer than N body-size components, split the heaviest by weighted k-means. Points are |d|-weighted centroids
(core>0: weights (|d| - core*sigma)+ to favour the high-contrast body over the soft shadow)."""
import numpy as np, cv2, json, sys, argparse, math
from scipy.optimize import linear_sum_assignment
ap = argparse.ArgumentParser()
ap.add_argument('stack'); ap.add_argument('timeline'); ap.add_argument('manifest'); ap.add_argument('out')
ap.add_argument('--k', type=float, default=4.0); ap.add_argument('--bg', default='global')
ap.add_argument('--core', type=float, default=0.0); ap.add_argument('--n', type=int, default=2)
ap.add_argument('--min_area_h2', type=float, default=0.02); ap.add_argument('--body_area_h2', type=float, default=0.06)
ap.add_argument('--causal_s', type=float, default=30.0)
a = ap.parse_args()
man = json.load(open(a.manifest)); cfg = man['config']; fps = man.get('fps', 19.8)
x0, y0 = cfg['roi_x'], cfg['roi_y']; H = float(cfg['person_height_px'])
S = np.load(a.stack).astype(np.float32); T = len(S); ds = 4
hd = H / ds
hp = S[1:200] - S[:199]; sigma = 1.4826 * np.median(np.abs(hp)) / math.sqrt(2)
ref_cfg = man.get('reference', {}); minc = ref_cfg.get('min_conf', 0.5); spots = ref_cfg.get('exclude_spots', [])
tl = {r['frame']: r for r in json.load(open(a.timeline))}
ko = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)); kc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
def wkmeans2(xs, ys, w, it=10):
    P = np.stack([xs, ys], 1).astype(np.float32)
    # init along the principal axis
    m = (P * w[:, None]).sum(0) / w.sum(); C = np.cov((P - m).T, aweights=w)
    ev, evec = np.linalg.eigh(C); ax = evec[:, -1]; s = math.sqrt(max(ev[-1], 1e-6))
    c = np.stack([m - ax * s, m + ax * s])
    for _ in range(it):
        lab = ((P[:, None, :] - c[None]) ** 2).sum(2).argmin(1)
        for j in range(2):
            mm = lab == j
            if mm.any(): c[j] = (P[mm] * w[mm, None]).sum(0) / w[mm].sum()
    return c, lab
frames = []
Bg = np.median(S[::4], 0) if a.bg == 'global' else None
Bc = None; Bc_i = -999
for i in range(T):
    if a.bg == 'causal':
        if Bc is None or i - Bc_i >= 20:
            lo = max(0, i - int(a.causal_s * fps)); idx = list(range(lo, max(i, lo + 1), 8))
            if len(idx) < 8: idx = list(range(0, min(T, 160), 8))   # bootstrap: the first 8 s (not causal for the first seconds)
            Bc = np.median(S[idx], 0); Bc_i = i
        B = Bc
    else:
        B = Bg
    d = S[i] - B; ad = np.abs(d)
    m = (ad > a.k * sigma).astype(np.uint8)
    m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, ko), cv2.MORPH_CLOSE, kc)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    comps = []
    for j in range(1, n):
        area = st[j, cv2.CC_STAT_AREA]
        if area < a.min_area_h2 * hd * hd: continue
        ys, xs = np.nonzero(lab == j)
        w = ad[ys, xs] if a.core <= 0 else np.maximum(ad[ys, xs] - a.core * sigma, 0) + 1e-3
        comps.append((float(w.sum()), area, xs, ys, w))
    comps.sort(key=lambda c: -c[0])
    pts = []
    body = [c for c in comps if c[1] >= a.body_area_h2 * hd * hd]
    if len(body) >= a.n:
        for c in body[:a.n]: pts.append(((c[2] * c[4]).sum() / c[4].sum(), (c[3] * c[4]).sum() / c[4].sum(), c[0]))
    elif len(body) == 1 and a.n == 2:
        c = body[0]; cc, labk = wkmeans2(c[2], c[3], c[4])
        for j in range(2): pts.append((cc[j][0], cc[j][1], float(c[4][labk == j].sum())))
    elif comps:
        for c in comps[:a.n]: pts.append(((c[2] * c[4]).sum() / c[4].sum(), (c[3] * c[4]).sum() / c[4].sum(), c[0]))
    P = [[float(px * ds + x0 + ds / 2), float(py * ds + y0 + ds / 2), float(ms)] for px, py, ms in pts]
    allc = [[float((c[2] * c[4]).sum() / c[4].sum() * ds + x0 + ds / 2), float((c[3] * c[4]).sum() / c[4].sum() * ds + y0 + ds / 2), int(c[1] * ds * ds), float(c[0])] for c in comps]
    frames.append({'frame': i, 'pts': P, 'comps': allc, 'nbody': len(body)})
# score
warm = man.get('warmup', 15)
tot = hit = hit35 = n2 = 0; offs = []
for fr in frames:
    i = fr['frame']
    if i < warm: continue
    n2 += len(fr['pts']) >= a.n
    r = tl.get(i)
    if not r: continue
    refs = [q for q in (r.get('ref') or []) if (q['conf'] is None or q['conf'] >= minc)
            and not any(math.hypot(q['c'][0] - sx, q['c'][1] - sy) <= sr for sx, sy, sr in spots)]
    refs = sorted(refs, key=lambda q: -(q['conf'] or 1))[:a.n]
    if not refs: continue
    if not fr['pts']: tot += len(refs); continue
    D = np.array([[math.hypot(p[0] - q['c'][0], p[1] - q['c'][1]) / q['h'] for p in fr['pts']] for q in refs])
    ri, pi = linear_sum_assignment(D)
    tot += len(refs)
    for a_, b_ in zip(ri, pi):
        q = refs[a_]; p = fr['pts'][b_]
        if D[a_, b_] <= 0.75: hit += 1; offs.append(((p[0] - q['c'][0]) / q['h'], (p[1] - q['c'][1]) / q['h']))
        if D[a_, b_] <= 0.35: hit35 += 1
offs = np.array(offs)
summ = {'frames': len(frames), 'sigma': round(float(sigma), 3), 'k': a.k, 'bg': a.bg, 'core': a.core,
        'frames_with_N_points': round(n2 / max(1, len(frames) - warm), 4),
        'onD_0.75h (assigned, vs YOLO>=%.2f)' % minc: round(hit / max(1, tot), 4),
        'onD_0.35h': round(hit35 / max(1, tot), 4), 'refs': tot,
        'offset_dx_h_median': round(float(np.median(offs[:, 0])), 3) if len(offs) else None,
        'offset_dy_h_median': round(float(np.median(offs[:, 1])), 3) if len(offs) else None,
        'offset_abs_h_p50': round(float(np.median(np.hypot(offs[:, 0], offs[:, 1]))), 3) if len(offs) else None,
        'offset_abs_h_p90': round(float(np.percentile(np.hypot(offs[:, 0], offs[:, 1]), 90)), 3) if len(offs) else None,
        'nbody_hist': {str(k): sum(1 for f in frames if f['nbody'] == k) for k in range(0, 5)}}
print(json.dumps(summ, indent=1))
json.dump({'summary': summ, 'frames': frames}, open(a.out, 'w'))
