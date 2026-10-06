import cv2, numpy as np, json, sys
fg = json.load(open(sys.argv[1])); tl = json.load(open(sys.argv[2])); video = sys.argv[3]; out = sys.argv[4]
frames = [int(x) for x in sys.argv[5].split(',')]
x0,y0,w,h = fg['roi']
byf = {r['frame']: r for r in fg['frames']}; tlf = {r['frame']: r for r in tl}
cap = cv2.VideoCapture(video); tiles = []
for i in frames:
    cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, f = cap.read()
    g = f[y0:y0+h, x0:x0+w, 0].astype(np.float32); lo, hi = np.percentile(g, [0.5, 99.5])
    b = cv2.cvtColor(np.clip((g-lo)/(hi-lo)*255, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    for c in byf[i]['comps'][:4]:
        bx, by, bw, bh = c['bbox']; cv2.rectangle(b, (bx-x0, by-y0), (bx-x0+bw, by-y0+bh), (0,200,255), 2)
        cv2.circle(b, (int(c['c'][0]-x0), int(c['c'][1]-y0)), 9, (0,200,255), -1)
    for r in tlf[i].get('ref') or []:
        col = (0,255,0) if (r['conf'] or 1) >= 0.5 else (0,120,0)
        cv2.drawMarker(b, (int(r['c'][0]-x0), int(r['c'][1]-y0)), col, cv2.MARKER_CROSS, 30, 3)
    for t in (tlf[i].get('emitted') or {}).get('tracks', []):
        col = (255,0,255) if t['state'] == 'live' else (255,128,0)
        cv2.drawMarker(b, (int(t['centroid'][0]-x0), int(t['centroid'][1]-y0)), col, cv2.MARKER_TILTED_CROSS, 30, 3)
    b = cv2.resize(b, (b.shape[1]//2, b.shape[0]//2)); cv2.putText(b, str(i), (5, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,255), 2)
    tiles.append(b)
rows = [np.hstack(tiles[k:k+4]) for k in range(0, len(tiles), 4)]
wmax = max(r.shape[1] for r in rows); rows = [np.pad(r, ((0,0),(0,wmax-r.shape[1]),(0,0))) for r in rows]
cv2.imwrite(out, np.vstack(rows))
