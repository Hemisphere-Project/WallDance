import os as _os_wd, sys as _sys_wd
os = _os_wd
_REPO = next((r for r in ('.', '..', '../..', '/data/WallDance') if os.path.isdir(os.path.join(r, 'application', 'tests'))), '..')
import sys, numpy as np, cv2, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(_REPO, 'application', 'tests')); import scoring
from fg_lib import compute_fg
man = scoring.load_scenario(sys.argv[2]); cfg = man['config']; H = float(cfg['person_height_px'])
S = np.load(sys.argv[1]).astype(np.float32); frames = [int(x) for x in sys.argv[3].split(',')]
tiles = []
for bg in ('causal', 'global'):
    fg, sig = compute_fg(S[:max(frames)+1] if bg == 'causal' else S, cfg['roi_x'], cfg['roi_y'], H, man['fps'], bg=bg)
    for f in frames:
        m = fg[f]['mask'] * 255; t = cv2.cvtColor(m.astype(np.uint8), cv2.COLOR_GRAY2BGR)
        for p in fg[f]['pts']: cv2.circle(t, (int((p[0]-cfg['roi_x'])/4), int((p[1]-cfg['roi_y'])/4)), 5, (0, 200, 255), 2)
        cv2.putText(t, f'{bg} {f} nb={fg[f]["nbody"]}', (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1); tiles.append(t)
n = len(frames); cv2.imwrite(sys.argv[4], np.vstack([np.hstack(tiles[:n]), np.hstack(tiles[n:])]))
