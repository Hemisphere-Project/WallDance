import os as _os_wd, sys as _sys_wd
os = _os_wd
_REPO = next((r for r in ('.', '..', '../..', '/data/WallDance') if os.path.isdir(os.path.join(r, 'application', 'tests'))), '..')
import sys, json, math, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(_REPO, 'application', 'tests'))
import scoring
from fg_lib import compute_fg, support
stack, tlp, manp, bg = sys.argv[1:5]
man = scoring.load_scenario(manp); cfg = man['config']; H = float(cfg['person_height_px']); x0, y0 = cfg['roi_x'], cfg['roi_y']
spots = man.get('reference', {}).get('exclude_spots', [])
S = np.load(stack).astype(np.float32); fg, sig = compute_fg(S, x0, y0, H, man['fps'], bg=bg); del S
tl = scoring._load_timeline(tlp)
cnt = {'kept': 0, 'rej_spot': 0, 'rej_near_fgpt': 0, 'rej_other': 0, 'kept_spot': 0}
conf_rej = []
rej_frames = []
for r in tl:
    f = r['frame']; fr = fg[f]
    for q in r.get('ref') or []:
        sp = support(fr['mask'], x0, y0, 4, q['c'][0], q['c'][1], q['h'])
        at_spot = any(math.hypot(q['c'][0]-sx, q['c'][1]-sy) <= sr for sx, sy, sr in spots)
        if sp >= 0.10:
            cnt['kept'] += 1; cnt['kept_spot'] += at_spot; continue
        near = any(math.hypot(q['c'][0]-p[0], q['c'][1]-p[1]) < 0.5*q['h'] for p in fr['pts'])
        k = 'rej_spot' if at_spot else ('rej_near_fgpt' if near else 'rej_other')
        cnt[k] += 1; conf_rej.append(q['conf'] or 1)
        if k == 'rej_other': rej_frames.append((f, q['c'], q['h'], q['conf']))
print(cnt); print('rejected conf p50/p90/max', np.percentile(conf_rej, [50, 90, 100]).round(2) if conf_rej else None)
import collections
loc = collections.Counter((int(c[0]//100*100), int(c[1]//100*100)) for f, c, h, cf in rej_frames)
print('rej_other by 100px cell', loc.most_common(8)); print('examples', rej_frames[:3], rej_frames[len(rej_frames)//2:len(rej_frames)//2+3])
