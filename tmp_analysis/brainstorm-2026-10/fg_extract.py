"""Foreground-mass extraction on a recording: clean plate (temporal median) + |I-B| threshold + CC.
Writes per-frame components (downscaled-ROI coords mapped back to original px) to an .npz/.json.
usage: fg_extract.py VIDEO ROI(x,y,w,h) OUT_PREFIX [--ds 4] [--k 4.0] [--bg global|causal]
"""
import cv2, numpy as np, json, sys, argparse, time
ap = argparse.ArgumentParser()
ap.add_argument('video'); ap.add_argument('roi'); ap.add_argument('out')
ap.add_argument('--ds', type=int, default=4); ap.add_argument('--k', type=float, default=4.0)
ap.add_argument('--bg', default='global'); ap.add_argument('--start', type=int, default=0)
ap.add_argument('--frames', type=int, default=0); ap.add_argument('--bg_step', type=int, default=4)
ap.add_argument('--causal_win_s', type=float, default=20.0); ap.add_argument('--fps', type=float, default=19.8)
ap.add_argument('--min_area_frac', type=float, default=0.015)  # of h_ds^2
ap.add_argument('--h', type=float, default=206.0)
a = ap.parse_args()
x0, y0, w, h = [int(v) for v in a.roi.split(',')]
ds = a.ds
cap = cv2.VideoCapture(a.video)
N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
end = N if a.frames <= 0 else min(N, a.start + a.frames)
t0 = time.time()
small = []
cap.set(cv2.CAP_PROP_POS_FRAMES, a.start)
for i in range(a.start, end):
    ok, f = cap.read()
    if not ok: break
    g = f[y0:y0+h, x0:x0+w, 0]
    s = cv2.resize(g, (w//ds, h//ds), interpolation=cv2.INTER_AREA).astype(np.float32)
    small.append(s)
small = np.stack(small)  # T,H,W
print('decoded', small.shape, '%.1fs' % (time.time()-t0), file=sys.stderr)
T = len(small)
# background(s)
if a.bg == 'global':
    B = np.median(small[::a.bg_step], axis=0)
    Bs = None
else:
    Bs = {}
# noise sigma from temporal MAD of the high-pass residual (global)
hp = small[1:] - small[:-1]
sigma = 1.4826 * np.median(np.abs(hp)) / np.sqrt(2)
print('noise sigma (ds px) %.3f' % sigma, file=sys.stderr)
hds = a.h / ds
min_area = a.min_area_frac * hds * hds
ker_o = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
ker_c = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
win = int(a.causal_win_s * a.fps)
res = []
energy = []
cache_B = None; cache_i = -999
for i in range(T):
    if a.bg != 'global':
        # causal running median: frames [i-win, i) sampled every bg_step*2, recomputed every 20 frames
        if i - cache_i >= 20 or cache_B is None:
            lo = max(0, i - win)
            idx = list(range(lo, max(lo+1, i), a.bg_step*2))
            cache_B = np.median(small[idx], axis=0) if len(idx) >= 5 else np.median(small[:max(5, i+1):a.bg_step], axis=0)
            cache_i = i
        B = cache_B
    d = small[i] - B
    ad = np.abs(d)
    m = (ad > a.k * sigma).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, ker_o)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, ker_c)
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
    comps = []
    for j in range(1, n):
        if st[j, cv2.CC_STAT_AREA] < min_area: continue
        ys, xs = np.nonzero(lab == j)
        wts = ad[ys, xs]
        mass = float(wts.sum())
        cx = float((xs * wts).sum() / mass); cy = float((ys * wts).sum() / mass)
        bx, by, bw, bh = st[j, :4]
        comps.append({'c': [round(cx*ds + x0 + ds/2, 1), round(cy*ds + y0 + ds/2, 1)], 'area': int(st[j, cv2.CC_STAT_AREA])*ds*ds,
                      'mass': round(mass*ds*ds, 1), 'bbox': [int(bx*ds+x0), int(by*ds+y0), int(bw*ds), int(bh*ds)],
                      'dark': round(float((d[ys, xs] < 0).mean()), 2)})
    comps.sort(key=lambda c: -c['mass'])
    res.append({'frame': a.start + i, 'comps': comps})
    # frame-difference energy for reference
    if i > 0:
        fd = np.abs(small[i] - small[i-1]); energy.append(float((fd > a.k*sigma*1.414).mean()))
json.dump({'video': a.video, 'roi': [x0, y0, w, h], 'ds': ds, 'k': a.k, 'sigma': float(sigma), 'bg': a.bg, 'frames': res,
           'fd_energy': energy}, open(a.out + '.json', 'w'))
if a.bg == 'global':
    cv2.imwrite(a.out + '_bg.png', np.clip(B, 0, 255).astype(np.uint8))
print('done %.1fs' % (time.time()-t0), file=sys.stderr)
