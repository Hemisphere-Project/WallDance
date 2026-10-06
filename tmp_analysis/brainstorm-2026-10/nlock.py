"""N-lock: a deliberately simple known-N tracker fed by (fg-verified) YOLO detections + clean-plate foreground
points, scored with the repo's own output_quality.compare_streams on a replay timeline.
usage: nlock.py STACK.npy TIMELINE.json MANIFEST.json [--variant fg|yolo|fusion] [--bg global|causal] [--tau 0.25]
Always emits exactly N points once initialised (state live = YOLO, fg = foreground, coasting = held)."""
import os as _os_wd, sys as _sys_wd
os = _os_wd
_REPO = next((r for r in ('.', '..', '../..', '/data/WallDance') if os.path.isdir(os.path.join(r, 'application', 'tests'))), '..')
import sys, json, math, argparse
import numpy as np
sys.path.insert(0, os.path.join(_REPO, 'application', 'tests')); sys.path.insert(0, os.path.join(_REPO, 'application', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scipy.optimize import linear_sum_assignment
import output_quality, scoring
from core.identity_slots import OneEuro2D, stability_params
from fg_lib import compute_fg, support

ap = argparse.ArgumentParser()
ap.add_argument('stack'); ap.add_argument('timeline'); ap.add_argument('manifest')
ap.add_argument('--variant', default='fusion'); ap.add_argument('--bg', default='global')
ap.add_argument('--tau', type=float, default=0.25); ap.add_argument('--smin', type=float, default=0.10)
ap.add_argument('--stability', type=float, default=0.5); ap.add_argument('--reacq_s', type=float, default=0.3)
ap.add_argument('--n', type=int, default=0); ap.add_argument('--k', type=float, default=4.0)
ap.add_argument('--core', type=float, default=8.0); ap.add_argument('--offset', type=int, default=1)
ap.add_argument('--dump', default=None); ap.add_argument('--json', default=None)
ap.add_argument('--gain_norm', type=int, default=0); ap.add_argument('--max_frame', type=int, default=0)
ap.add_argument('--plate', default=None, help='frame range a:b to build the clean plate from (bg=plate)')
ap.add_argument('--plate_npy', default=None, help='empty-wall take decoded by decode_cache.py (same ROI and downscale) as the plate')
a = ap.parse_args()
man = scoring.load_scenario(a.manifest); cfg = man['config']; fps = man.get('fps', 19.8)
N = a.n or max(1, scoring.max_expected(man)); H = float(cfg['person_height_px'])
x0, y0 = cfg['roi_x'], cfg['roi_y']; ds = 4
rows = scoring._load_timeline(a.timeline)
if a.max_frame: rows = [r for r in rows if r['frame'] < a.max_frame]
need_fg = a.variant in ('fg', 'fusion', 'yolo_verified')
fg = None
if need_fg:
    S = np.load(a.stack).astype(np.float32)
    bgf = None
    if a.plate: lo, hi = [int(v) for v in a.plate.split(':')]; bgf = list(range(lo, hi))
    plate = np.load(a.plate_npy).astype(np.float32) if a.plate_npy else None
    fg, sigma = compute_fg(S, x0, y0, H, fps, n=N, k=a.k, bg=a.bg, core=a.core, bg_frames=bgf, gain_norm=bool(a.gain_norm),
                           plate=plate)
    del S
mn, beta = stability_params(a.stability)

class Slot:
    def __init__(s, sid): s.sid = sid; s.pos = None; s.vel = np.zeros(2); s.last = -1e9; s.last_strong = -1e9; s.off = np.zeros(2); s.noff = 0; s.f = OneEuro2D(mn, beta); s.state = 'lost'
slots = [Slot(i + 1) for i in range(N)]
out = []; tprev = None; stats = {'live': 0, 'fg': 0, 'coasting': 0, 'ghost_rejected': 0, 'reacq': 0}
for idx, r in enumerate(sorted(rows, key=lambda r: r['frame'])):
    f = r['frame']; t = r.get('abs_frame', f) / fps; dt = 1.0 / fps if tprev is None else min(0.25, t - tprev); tprev = t
    fr = fg[f] if fg is not None and f < len(fg) else None
    # candidates
    strong = []
    if a.variant in ('yolo', 'fusion', 'yolo_verified'):
        for q in r.get('ref') or []:
            if q['conf'] is not None and q['conf'] < a.tau: continue
            if a.variant in ('fusion', 'yolo_verified') and fr is not None:
                sp = support(fr['mask'], x0, y0, ds, q['c'][0], q['c'][1], q['h'])
                if sp < a.smin: stats['ghost_rejected'] += 1; continue
            strong.append(np.array(q['c'], float))
    weak = [np.array(p[:2], float) for p in fr['pts']] if (fr is not None and a.variant in ('fg', 'fusion')) else []
    # predictions
    pred = {}
    for s in slots:
        if s.pos is None: continue
        decay = math.exp(-(t - s.last) / 0.25) if t - s.last < 2 else 0.0
        pred[s.sid] = s.pos + s.vel * dt * decay
    meas = {}; kind = {}
    used_s = set(); used_w = set()
    def hung(cands, used, gate_fn, tag):
        free = [s for s in slots if s.pos is not None and s.sid not in meas]
        ci = [j for j in range(len(cands)) if j not in used]
        if not free or not ci: return
        C = np.array([[np.linalg.norm(cands[j] - pred[s.sid]) / H for j in ci] for s in free])
        ri, cj = linear_sum_assignment(C)
        for i_, j_ in zip(ri, cj):
            s = free[i_]
            if C[i_, j_] <= gate_fn(s): meas[s.sid] = cands[ci[j_]]; kind[s.sid] = tag; used.add(ci[j_])
    gate = lambda base: (lambda s: min(3.0, base + 2.0 * max(0.0, t - s.last - 1.0 / fps)))
    hung(strong, used_s, gate(1.0), 'live')
    # a weak point explained by a strong measurement (same body) cannot feed another slot
    for j, w in enumerate(weak):
        if any(np.linalg.norm(w - strong[k]) < 0.5 * H for k in used_s): used_w.add(j)
    hung([w + 0 for w in weak], used_w, gate(0.75), 'fg')
    # initialise / re-acquire anywhere (known N: a slot with no evidence takes unexplained evidence far from the others)
    for s in slots:
        if s.sid in meas: continue
        if s.pos is not None and t - s.last < a.reacq_s: continue
        others = [meas.get(o.sid, pred.get(o.sid)) for o in slots if o.sid != s.sid and (o.sid in meas or o.sid in pred)]
        def far(c): return all(o is None or np.linalg.norm(c - o) >= 1.0 * H for o in others)
        cand = [(j, c, 'live', used_s) for j, c in enumerate(strong) if j not in used_s and far(c)] + \
               [(j, c, 'fg', used_w) for j, c in enumerate(weak) if j not in used_w and far(c)]
        if cand:
            j, c, tag, used = cand[0]; used.add(j); meas[s.sid] = c; kind[s.sid] = tag
            if s.pos is not None: stats['reacq'] += 1
            s.f.reset(); s.vel[:] = 0; s.pos = None
    # offsets (fg point -> YOLO box centre) learned while both see the same body
    if a.offset:
        for s in slots:
            if kind.get(s.sid) == 'live':
                near = [w for w in weak if np.linalg.norm(w - meas[s.sid]) < 0.5 * H]
                if near:
                    w = min(near, key=lambda w: np.linalg.norm(w - meas[s.sid])); o = meas[s.sid] - w
                    s.off = o if s.noff == 0 else 0.9 * s.off + 0.1 * o; s.noff += 1
    tracks = []
    for s in slots:
        if s.sid in meas:
            z = meas[s.sid] + (s.off if (kind[s.sid] == 'fg' and a.offset) else 0)
            if s.pos is not None: s.vel = 0.6 * s.vel + 0.4 * (z - s.pos) / max(dt, 1e-3)
            s.pos = z; s.last = t; s.state = kind[s.sid]
            if kind[s.sid] == 'live': s.last_strong = t
        elif s.pos is not None:
            s.pos = pred[s.sid]; s.state = 'coasting'
        else:
            continue
        o = s.f(s.pos, t, scale=H)
        stats[s.state] += 1
        tracks.append({'id': s.sid, 'bbox': [o[0] - 0.2 * H, o[1] - 0.5 * H, 0.4 * H, H], 'centroid': [float(o[0]), float(o[1])],
                       'state': 'live' if s.state in ('live', 'fg') else 'coasting', 'src': s.state})
    row = dict(r); row['emitted'] = {'reported': len(tracks), 'ids': sorted(x['id'] for x in tracks), 'tracks': tracks}
    out.append(row)
rep = output_quality.compare_streams(out, man, fps=fps)
e = rep['emitted']; c = e['continuity']; q = e['quality']
tot = sum(stats[k] for k in ('live', 'fg', 'coasting'))
res = {'variant': a.variant, 'bg': a.bg, 'tau': a.tau, '2pts(cov)': c['coverage'], 'onD': c['on_dancer'], 'spat': c['spatial_validity'],
       'hole_max_s': c['gap_max_s'], 'coast_share': round(stats['coasting'] / max(1, tot), 4), 'fg_share': round(stats['fg'] / max(1, tot), 4),
       'jit_rest_pct': q.get('jitter_rest_pct'), 'lag_ms': q.get('lag_ms'), 'fastE': q.get('fast_err_h'), 'id_switches': q.get('id_switches'),
       'ghost_rejected_dets': stats['ghost_rejected'], 'reacq': stats['reacq']}
print(json.dumps(res))
if a.json: json.dump(res, open(a.json, 'w'))
if a.dump: json.dump(out, open(a.dump, 'w'))
