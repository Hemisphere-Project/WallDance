"""'Look deeper' feasibility: does a local YOLO pass (crop around the missed dancer, higher res, low conf) find
dancers the full-frame pass missed at tau?  Candidates = clean-plate fg points >= 0.75 h from every YOLO det >= tau.
Controls = empty-wall crops (no fg).  PyTorch yolo11x-pose on the dev GPU, enhancement approximated with cv2.
usage: lookdeeper.py VIDEO STACK.npy TIMELINE MANIFEST OUT.json [max_cands]"""
import os as _os_wd, sys as _sys_wd
os = _os_wd
_REPO = next((r for r in ('.', '..', '../..', '/data/WallDance') if os.path.isdir(os.path.join(r, 'application', 'tests'))), '..')
import sys, json, math, random, time
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(_REPO, 'application', 'tests'))
import scoring
from fg_lib import compute_fg
video, stack, tlp, manp, outp = sys.argv[1:6]; maxc = int(sys.argv[6]) if len(sys.argv) > 6 else 120
man = scoring.load_scenario(manp); cfg = man['config']; x0, y0, w, h = cfg['roi_x'], cfg['roi_y'], cfg['roi_w'], cfg['roi_h']
H = float(cfg['person_height_px']); tau = float(cfg.get('confidence', 0.25)); gamma = float(cfg.get('gamma', 2.2)); clip = float(cfg.get('clahe_clip', 2.5))
spots = man.get('reference', {}).get('exclude_spots', [])
S = np.load(stack).astype(np.float32); fg, _ = compute_fg(S, x0, y0, H, man['fps'], bg='global', gain_norm=True, keep_masks=True); del S
tl = {r['frame']: r for r in scoring._load_timeline(tlp)}
cands = []; ctrls = []
for f, r in tl.items():
    if f < 15: continue
    dets = [q for q in r.get('ref') or [] if (q['conf'] or 1) >= tau]
    for p in fg[f]['pts']:
        if all(math.hypot(p[0]-q['c'][0], p[1]-q['c'][1]) > 0.75*q['h'] for q in dets): cands.append((f, p[0], p[1]))
random.seed(3); random.shuffle(cands); cands = sorted(cands[:maxc])
# controls: random wall points with no fg within 1.5 h, away from the known ghost spots
for f, _, _ in cands[: maxc // 2]:
    m = fg[f]['mask']
    for _ in range(50):
        cx, cy = random.uniform(x0 + H, x0 + w - H), random.uniform(y0 + H, y0 + h - H)
        if any(math.hypot(cx-sx, cy-sy) < sr + H for sx, sy, sr in spots): continue
        mx0, my0 = int((cx - 1.5*H - x0)/4), int((cy - 1.5*H - y0)/4)
        if m[max(0, my0):my0 + int(3*H/4), max(0, mx0):mx0 + int(3*H/4)].sum() == 0: ctrls.append((f, cx, cy)); break
print('candidates', len(cands), 'controls', len(ctrls), file=sys.stderr)
from ultralytics import YOLO
model = YOLO('/data/WallDance/models/yolo11x-pose.pt')
lut = (np.clip(((np.arange(256) / 255.0) ** (1.0 / gamma)) * 255, 0, 255)).astype(np.uint8)
cl = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
cap = cv2.VideoCapture(video); cache = {}
def roi_img(f):
    if f not in cache:
        cap.set(cv2.CAP_PROP_POS_FRAMES, f); ok, im = cap.read(); g = cl.apply(lut[im[y0:y0+h, x0:x0+w, 0]])
        cache.clear(); cache[f] = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    return cache[f]
def best_conf(res, ox, oy, sc, cx, cy, hh):
    b = res[0].boxes; best = 0.0
    if b is None or len(b) == 0: return 0.0
    for (bx0, by0, bx1, by1), c in zip(b.xyxy.cpu().numpy(), b.conf.cpu().numpy()):
        mx, my = ox + (bx0 + bx1) / 2 / sc, oy + (by0 + by1) / 2 / sc
        if math.hypot(mx - cx, my - cy) <= 0.75 * hh: best = max(best, float(c))
    return best
def run(points, tag):
    out = []; t_full = t_crop = 0.0; n = 0
    for f, cx, cy in points:
        img = roi_img(f); n += 1
        t0 = time.time(); rf = model.predict(img, imgsz=1280, conf=0.03, verbose=False, half=True); t_full += time.time() - t0
        c_full = best_conf(rf, x0, y0, 1.0, cx, cy, H)
        half = int(1.25 * H); lx, ly = int(cx - x0 - half), int(cy - y0 - half)
        crop = np.zeros((2*half, 2*half, 3), np.uint8)
        sx0, sy0, sx1, sy1 = max(0, lx), max(0, ly), min(w, lx + 2*half), min(h, ly + 2*half)
        crop[sy0-ly:sy1-ly, sx0-lx:sx1-lx] = img[sy0:sy1, sx0:sx1]
        rec = {'frame': f, 'x': round(cx, 1), 'y': round(cy, 1), 'full_1280': round(c_full, 3)}
        for isz in (640, 960):
            t0 = time.time(); up = cv2.resize(crop, (isz, isz), interpolation=cv2.INTER_LINEAR)
            rc = model.predict(up, imgsz=isz, conf=0.03, verbose=False, half=True); t_crop += time.time() - t0
            rec[f'crop_{isz}'] = round(best_conf(rc, x0 + lx, y0 + ly, isz / (2*half), cx, cy, H), 3)
        out.append(rec)
    print(tag, 'n', n, 'ms/full %.1f ms/crop-pair %.1f' % (1000*t_full/max(1, n), 1000*t_crop/max(1, n)), file=sys.stderr)
    return out
model.predict(np.zeros((640, 640, 3), np.uint8), imgsz=640, verbose=False, half=True)
C = run(cands, 'cands'); K = run(ctrls, 'ctrls')
def rate(rows, key, thr): return round(sum(r[key] >= thr for r in rows) / max(1, len(rows)), 3)
summ = {}
for name, rows in (('missed-dancer candidates', C), ('empty-wall controls', K)):
    summ[name] = {'n': len(rows)}
    for key in ('full_1280', 'crop_640', 'crop_960'):
        summ[name][key] = {f'>={t}': rate(rows, key, t) for t in (0.05, 0.10, 0.25)}
print(json.dumps(summ, indent=1))
json.dump({'summary': summ, 'cands': C, 'ctrls': K}, open(outp, 'w'))
