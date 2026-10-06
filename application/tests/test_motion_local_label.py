"""extract_local_motion_blob's component pick: the screened selection must equal
the original per-label loop exactly (incl. distance ties broken by area, then
by the earliest label), and the query-box-only MOG2 threshold must not change
any blob or ratio."""
import cv2
import numpy as np

from core.motion_detector import MotionDetector


def _loop_pick(n_labels, stats, centroids, x1, y1, target_small):
    """The pre-PERF implementation, verbatim."""
    best_label = None
    best_key = None
    for label in range(1, n_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area <= 0:
            continue
        cx = float(centroids[label][0] + x1)
        cy = float(centroids[label][1] + y1)
        if target_small is not None:
            dist = float(np.linalg.norm(np.array([cx, cy]) - target_small))
            key = (dist, -area)
        else:
            key = (-area,)
        if best_key is None or key < best_key:
            best_key = key
            best_label = label
    return best_label


def _masks(rng):
    for _ in range(150):                          # fragmented noise masks
        m = (rng.random((rng.integers(5, 90), rng.integers(5, 90))) > rng.uniform(0.4, 0.9))
        yield m.astype(np.uint8) * 255
    for gap in range(1, 12):                       # symmetric twins: exact distance ties
        m = np.zeros((41, 41), np.uint8)
        m[20, 20 - gap] = m[20, 20 + gap] = 255
        m[20 - gap, 20] = 255
        yield m
        m2 = m.copy()
        m2[19, 20 + gap] = 255                     # same distance class, bigger area
        yield m2


def test_screened_pick_equals_the_loop():
    rng = np.random.default_rng(3)
    n = 0
    for m in _masks(rng):
        n_labels, _lab, stats, centroids = cv2.connectedComponentsWithStats(m, connectivity=8)
        h, w = m.shape
        x1, y1 = int(rng.integers(0, 400)), int(rng.integers(0, 400))
        targets = [None, np.array([x1 + w / 2.0, y1 + h / 2.0]),
                   np.array([x1 + 20.0, y1 + 20.0]),
                   np.array([x1 + rng.uniform(-50, 150), y1 + rng.uniform(-50, 150)]) * 1.0]
        for t in targets:
            assert (MotionDetector._select_local_label(n_labels, stats, centroids, x1, y1, t)
                    == _loop_pick(n_labels, stats, centroids, x1, y1, t))
            n += 1
    assert n > 600


def test_query_box_threshold_matches_full_mask_threshold():
    rng = np.random.default_rng(5)
    md = MotionDetector()
    md._scale = 0.7
    md._inv_scale = 1 / 0.7
    fg = rng.choice(np.array([0, 127, 255], np.uint8), size=(300, 400), p=[0.6, 0.15, 0.25])
    fg = cv2.dilate(fg, np.ones((3, 3), np.uint8))
    md._fg_mask = fg
    md._clean_mask = cv2.morphologyEx((fg == 255).astype(np.uint8) * 255, cv2.MORPH_OPEN,
                                      np.ones((3, 3), np.uint8))

    def old_extract(x, y, w, h, target, shadows):
        mask = ((fg >= 127) if shadows else (fg == 255)).astype(np.uint8) * 255
        s = md._scale
        sx, sy = max(0, int(x * s)), max(0, int(y * s))
        sw, sh = max(1, int(w * s)), max(1, int(h * s))
        mh, mw = mask.shape
        x1, y1 = min(sx, mw - 1), min(sy, mh - 1)
        x2, y2 = min(sx + sw, mw), min(sy + sh, mh)
        if x2 <= x1 or y2 <= y1:
            return None, 0.0
        roi = mask[y1:y2, x1:x2]
        cnt = int(np.count_nonzero(roi))
        if cnt <= 0:
            return None, 0.0
        ratio = cnt / roi.size
        if ratio < 0.02:
            return None, ratio
        n, _l, st, ce = cv2.connectedComponentsWithStats(roi, connectivity=8)
        ts = None if target is None else np.array(target, np.float64) * s
        lab = _loop_pick(n, st, ce, x1, y1, ts)
        if lab is None:
            return None, ratio
        return (int(st[lab, 0]) + x1, int(st[lab, 1]) + y1, int(st[lab, 2]),
                int(st[lab, 3]), float(ce[lab][0] + x1), float(ce[lab][1] + y1)), ratio

    for _ in range(200):
        x, y = rng.uniform(-20, 560), rng.uniform(-20, 420)
        w, h = rng.uniform(1, 300), rng.uniform(1, 300)
        target = None if rng.random() < 0.3 else (x + rng.uniform(0, w), y + rng.uniform(0, h))
        shadows = bool(rng.random() < 0.5)
        blob, ratio = md.extract_local_motion_blob(x, y, w, h, target_centroid=target,
                                                   include_shadows=shadows)
        ref, rref = old_extract(x, y, w, h, target, shadows)
        assert ratio == rref
        if ref is None:
            assert blob is None
        else:
            inv = md._inv_scale
            assert blob.bbox.tolist() == [ref[0] * inv, ref[1] * inv, ref[2] * inv, ref[3] * inv]
            assert blob.centroid.tolist() == [ref[4] * inv, ref[5] * inv]
        for shad in (False, True):
            old = ((fg >= 127) if shad else (fg == 255)).astype(np.uint8) * 255
            md2 = MotionDetector()
            md2._scale = 0.7
            md2._clean_mask = old                    # the old path thresholded first
            want = md2.motion_ratio_in_bbox(x, y, w, h, include_shadows=shad)
            got = md.motion_ratio_in_bbox(x, y, w, h, include_shadows=shad,
                                          use_clean_mask=False)
            assert got == want
