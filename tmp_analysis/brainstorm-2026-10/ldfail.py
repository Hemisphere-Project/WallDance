import json, cv2, numpy as np, sys
d = json.load(open('wd_lookdeeper.json')); x0, y0 = 137, 232; H = 206
fail = [r for r in d['cands'] if max(r['full_1280'], r['crop_640'], r['crop_960']) < 0.10]
ok = [r for r in d['cands'] if r['crop_640'] >= 0.10 and r['full_1280'] < 0.10]
print('fail', len(fail), 'crop-only rescues', len(ok))
cap = cv2.VideoCapture('/data/WallDance/projects/4_TANGO_HANGAR-whitebg3/recordings/slot_2_20260403_101623.avi')
def tiles(rows):
    out = []
    for r in rows[:12]:
        cap.set(cv2.CAP_PROP_POS_FRAMES, r['frame']); ok_, im = cap.read(); g = im[:, :, 0].astype(np.float32)
        half = int(1.5 * H); cx, cy = int(r['x']), int(r['y'])
        c = g[max(0, cy-half):cy+half, max(0, cx-half):cx+half]; lo, hi = np.percentile(c, [1, 99.5])
        c = cv2.cvtColor(np.clip((c-lo)/(hi-lo+1e-3)*255, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        c = cv2.resize(c, (240, 240)); cv2.drawMarker(c, (120, 120), (0, 200, 255), cv2.MARKER_CROSS, 30, 2)
        cv2.putText(c, f"{r['frame']} {r['full_1280']:.2f}/{r['crop_640']:.2f}", (3, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1); out.append(c)
    out += [np.zeros_like(out[0])] * (12 - len(out))
    return np.vstack([np.hstack(out[i:i+6]) for i in (0, 6)])
cv2.imwrite('ld_fail.jpg', tiles(fail)); cv2.imwrite('ld_rescue.jpg', tiles(ok))
