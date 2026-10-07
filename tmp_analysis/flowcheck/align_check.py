#!/usr/bin/env python3
"""Side-by-side of two recut takes (display-enhanced) with a grid, to eyeball that they show the same wall."""
import sys

import cv2
import numpy as np

a, b, out = sys.argv[1], sys.argv[2], sys.argv[3]
cl = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))


def first(p, n=30):
    cap = cv2.VideoCapture(p)
    fr = [cv2.cvtColor(cap.read()[1], cv2.COLOR_BGR2GRAY) for _ in range(n)]
    return cl.apply(np.median(np.stack(fr), axis=0).astype(np.uint8))


ims = []
for p in (a, b):
    g = cv2.cvtColor(first(p), cv2.COLOR_GRAY2BGR)
    for x in range(0, g.shape[1], 200):
        cv2.line(g, (x, 0), (x, g.shape[0]), (0, 200, 255), 1)
    for y in range(0, g.shape[0], 200):
        cv2.line(g, (0, y), (g.shape[1], y), (0, 200, 255), 1)
    ims.append(cv2.resize(g, (744, 650)))
cv2.imwrite(out, np.hstack(ims), [cv2.IMWRITE_JPEG_QUALITY, 75])
print(out)
