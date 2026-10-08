"""Far-wall scale on both nights: the lit roll-up door's width and height in px (edges of the row / column
mean profiles on an empty frame), same sensor pixels (the 25 m takes are a crop, not a resize)."""
import json, sys
import cv2, numpy as np
T = {"30m slot 3 end": ("/data/WallDance/projects/mur30m-0710/recordings/slot_3_20261007_201246.avi", 1140),
     "30m slot 5 end": ("/data/WallDance/projects/mur30m-0710/recordings/slot_5_20261007_202412.avi", 1090),
     "25m slot 1 (bright, empty)": ("/data/WallDance/projects/mur25m-night-common/recordings/slot_1_20261006_211624.avi", 200),
     "25m slot 2 21:40 (dark, empty)": ("/data/WallDance/projects/mur25m-night-common/recordings/slot_2_20261006_214010.avi", 100)}
out = {}
for k, (v, f) in T.items():
    cap = cv2.VideoCapture(v); cap.set(cv2.CAP_PROP_POS_FRAMES, f)
    acc = []
    for _ in range(20):
        ok, fr = cap.read()
        acc.append(fr[:, :, 0].astype(np.float32))
    g = np.mean(acc, axis=0)
    g = cv2.GaussianBlur(g, (0, 0), 2)
    # door: the bright block; its rows 450-700 vs the wall
    prof_x = g[450:700].mean(axis=0)
    thr_x = prof_x.min() + 0.5 * (np.percentile(prof_x, 99.5) - prof_x.min())
    xs = np.where(prof_x > thr_x)[0]
    # largest contiguous run
    runs, s = [], xs[0]
    for a, b in zip(xs[:-1], xs[1:]):
        if b != a + 1:
            runs.append((s, a)); s = b
    runs.append((s, xs[-1]))
    x0, x1 = max(runs, key=lambda r: r[1] - r[0])
    prof_y = g[:, x0 + 10:x1 - 10].mean(axis=1)
    thr_y = prof_y.min() + 0.5 * (np.percentile(prof_y, 99.5) - prof_y.min())
    ys = np.where(prof_y > thr_y)[0]
    runs, s = [], ys[0]
    for a, b in zip(ys[:-1], ys[1:]):
        if b != a + 1:
            runs.append((s, a)); s = b
    runs.append((s, ys[-1]))
    y0, y1 = max(runs, key=lambda r: r[1] - r[0])
    out[k] = {"door_x": [int(x0), int(x1)], "door_w": int(x1 - x0 + 1), "door_y": [int(y0), int(y1)],
              "door_h": int(y1 - y0 + 1), "door_dn_med": float(np.median(g[y0:y1, x0:x1]))}
    print(k, out[k])
json.dump(out, open("/data/WallDance/tmp_analysis/confirm_0710/door_scale.json", "w"), indent=1)
