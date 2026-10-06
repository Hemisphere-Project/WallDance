import cv2, numpy as np, sys
path, out = sys.argv[1], sys.argv[2]
frames = [int(x) for x in sys.argv[3].split(',')]
x0,y0,x1,y1 = [int(v) for v in sys.argv[4].split(',')]
cap = cv2.VideoCapture(path); tiles=[]
for i in frames:
    cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok,f = cap.read(); g=f[y0:y1,x0:x1,0].astype(np.float32)
    lo,hi = np.percentile(g,[0.5,99.5]); b=np.clip((g-lo)/(hi-lo)*255,0,255).astype(np.uint8)
    cv2.putText(b,str(i),(5,25),cv2.FONT_HERSHEY_SIMPLEX,0.8,255,2); tiles.append(b)
cv2.imwrite(out, np.hstack(tiles))
