import os as _os_wd, sys as _sys_wd
os = _os_wd
_REPO = next((r for r in ('.', '..', '../..', '/data/WallDance') if os.path.isdir(os.path.join(r, 'application', 'tests'))), '..')
import cv2, numpy as np, json, sys, math
sys.path.insert(0, os.path.join(_REPO, 'application', 'tests')); import scoring
video, sa, tlp, manp, out = sys.argv[1:6]; frames = [int(x) for x in sys.argv[6].split(',')]
man = scoring.load_scenario(manp); cfg = man['config']; x0, y0, w, h = cfg['roi_x'], cfg['roi_y'], cfg['roi_w'], cfg['roi_h']
A = {r['frame']: r for r in json.load(open(sa))}; T = {r['frame']: r for r in scoring._load_timeline(tlp)}
cap = cv2.VideoCapture(video); tiles = []
for f in frames:
    cap.set(cv2.CAP_PROP_POS_FRAMES, f); ok, im = cap.read(); g = im[y0:y0+h, x0:x0+w, 0].astype(np.float32)
    lo, hi = np.percentile(g, [0.5, 99.7]); b = cv2.cvtColor(np.clip((g-lo)/(hi-lo)*255, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    for q in T[f].get('ref') or []:
        col = (0, 255, 0) if (q['conf'] or 1) >= 0.5 else (0, 130, 0)
        cv2.drawMarker(b, (int(q['c'][0]-x0), int(q['c'][1]-y0)), col, cv2.MARKER_CROSS, 40, 4)
    for t in T[f]['emitted']['tracks']:
        col = (255, 0, 255) if t['state'] == 'live' else (255, 160, 0)
        cv2.drawMarker(b, (int(t['centroid'][0]-x0), int(t['centroid'][1]-y0)), col, cv2.MARKER_TILTED_CROSS, 44, 5)
    for t in A[f]['emitted']['tracks']:
        cv2.circle(b, (int(t['centroid'][0]-x0), int(t['centroid'][1]-y0)), 13, (0, 220, 255), 3)
    b = cv2.resize(b, (w//2, h//2)); cv2.putText(b, str(f), (5, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2); tiles.append(b)
grp = tiles + [np.zeros_like(tiles[0])] * (8 - len(tiles))
cv2.imwrite(out, np.vstack([np.hstack(grp[:4]), np.hstack(grp[4:8])]), [cv2.IMWRITE_JPEG_QUALITY, 85])
