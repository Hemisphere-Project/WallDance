import cv2, numpy as np, sys
path, out, idxs = sys.argv[1], sys.argv[2], [int(x) for x in sys.argv[3].split(',')]
cap = cv2.VideoCapture(path)
n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); print('frames', n, cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
tiles=[]
for i in idxs:
    cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, f = cap.read()
    if not ok: continue
    g = f[:,:,0].astype(np.float32)
    print(i, 'mean %.1f p50 %.0f p99 %.0f p99.9 %.0f max %.0f' % (g.mean(), *np.percentile(g,[50,99,99.9]), g.max()))
    b = np.clip(255*(g/max(1,np.percentile(g,99.5)))**0.5,0,255).astype(np.uint8)
    b = cv2.resize(b, (b.shape[1]//3, b.shape[0]//3))
    cv2.putText(b, str(i), (10,30), cv2.FONT_HERSHEY_SIMPLEX, 1, 255, 2)
    tiles.append(b)
row = np.hstack(tiles); cv2.imwrite(out, row)
