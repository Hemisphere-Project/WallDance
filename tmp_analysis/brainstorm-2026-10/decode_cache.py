"""Decode a recording once into a downscaled ROI stack (float16 .npy) for fast CPU experiments.
usage: decode_cache.py VIDEO x,y,w,h DS OUT.npy [start] [frames]"""
import cv2, numpy as np, sys, time
video, roi, ds, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
start = int(sys.argv[5]) if len(sys.argv) > 5 else 0; nfr = int(sys.argv[6]) if len(sys.argv) > 6 else 0
x0, y0, w, h = [int(v) for v in roi.split(',')]
cap = cv2.VideoCapture(video); N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); end = N if nfr <= 0 else min(N, start+nfr)
cap.set(cv2.CAP_PROP_POS_FRAMES, start); fr = []; t0 = time.time()
for i in range(start, end):
    ok, f = cap.read()
    if not ok: break
    fr.append(cv2.resize(f[y0:y0+h, x0:x0+w, 0].astype(np.float32), (w//ds, h//ds), interpolation=cv2.INTER_AREA).astype(np.float16))
np.save(out, np.stack(fr)); print('saved', len(fr), 'frames in %.0fs' % (time.time()-t0))
