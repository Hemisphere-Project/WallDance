"""Per-frame CPU cost of the clean-plate fg step on this box (i7-3770K, loaded), single thread, from a raw ROI frame."""
import cv2, numpy as np, time, sys
cv2.setNumThreads(1)
cap = cv2.VideoCapture(sys.argv[1]); x0, y0, w, h = 137, 232, 1299, 1139
frames = []
for i in range(60):
    ok, f = cap.read(); frames.append(np.ascontiguousarray(f[y0:y0+h, x0:x0+w, 0]))
B = np.median(np.stack([cv2.resize(f.astype(np.float32), (w//4, h//4), interpolation=cv2.INTER_AREA) for f in frames[::3]]), 0)
ko = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)); kc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
ts = {'resize': [], 'diff+thr+morph': [], 'cc+centroids': []}
for f in frames * 3:
    t0 = time.perf_counter(); s = cv2.resize(f, (w//4, h//4), interpolation=cv2.INTER_AREA).astype(np.float32); t1 = time.perf_counter()
    ad = cv2.absdiff(s, B); m = (ad > 2.4).astype(np.uint8); m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, ko), cv2.MORPH_CLOSE, kc); t2 = time.perf_counter()
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
    for j in range(1, n):
        if st[j, 4] < 50: continue
        ys, xs = np.nonzero(lab[st[j,1]:st[j,1]+st[j,3], st[j,0]:st[j,0]+st[j,2]] == j); wts = ad[ys + st[j,1], xs + st[j,0]]; (xs * wts).sum() / wts.sum()
    t3 = time.perf_counter()
    ts['resize'].append(t1 - t0); ts['diff+thr+morph'].append(t2 - t1); ts['cc+centroids'].append(t3 - t2)
tot = np.array(ts['resize']) + np.array(ts['diff+thr+morph']) + np.array(ts['cc+centroids'])
print({k: 'p50 %.2f ms p95 %.2f ms' % (1000*np.median(v), 1000*np.percentile(v, 95)) for k, v in ts.items()}, 'total p50 %.2f p95 %.2f ms' % (1000*np.median(tot), 1000*np.percentile(tot, 95)))
