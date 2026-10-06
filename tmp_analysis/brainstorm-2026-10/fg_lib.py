"""Shared helpers for the brainstorm experiments: clean-plate foreground on a downscaled ROI stack."""
import numpy as np, cv2, math

def wkmeans2(xs, ys, w, it=10):
    P = np.stack([xs, ys], 1).astype(np.float32)
    m = (P * w[:, None]).sum(0) / w.sum(); C = np.cov((P - m).T, aweights=w)
    ev, evec = np.linalg.eigh(C); ax = evec[:, -1]; s = math.sqrt(max(ev[-1], 1e-6))
    c = np.stack([m - ax * s, m + ax * s])
    for _ in range(it):
        lab = ((P[:, None, :] - c[None]) ** 2).sum(2).argmin(1)
        for j in range(2):
            mm = lab == j
            if mm.any(): c[j] = (P[mm] * w[mm, None]).sum(0) / w[mm].sum()
    return c, lab

def compute_fg(S, x0, y0, H, fps, n=2, k=4.0, bg='global', core=8.0, ds=4, min_area_h2=0.02,
               body_area_h2=0.06, causal_s=30.0, keep_masks=True, bg_frames=None, gain_norm=False,
               plate=None):
    """S: (T,h,w) float32 stack.  bg: 'global' (median of the take, non-causal), 'causal' (median of the
    previous causal_s seconds, refreshed every 20 frames), 'plate' (median of bg_frames = an empty-wall clip)."""
    T = len(S); hd = H / ds
    hp = S[1:200] - S[:199]; sigma = 1.4826 * np.median(np.abs(hp)) / math.sqrt(2)
    ko = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)); kc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    Bg = None
    if bg == 'global': Bg = np.median(S[::4], 0)
    if bg == 'plate':
        # plate = an external empty-wall stack (another take, same ROI / downscale), else frames of this one
        Bg = np.median(plate[::2], 0) if plate is not None else np.median(S[bg_frames], 0)
    Bc = None; Bc_i = -999
    out = []
    for i in range(T):
        if bg == 'causal':
            if Bc is None or i - Bc_i >= 20:
                lo = max(0, i - int(causal_s * fps)); idx = list(range(lo, max(i, lo + 1), 8))
                if len(idx) < 8: idx = list(range(0, min(T, 160), 8))
                Bc = np.median(S[idx], 0); Bc_i = i
            B = Bc
        else:
            B = Bg
        thr = k * sigma
        if gain_norm:
            sub_i = S[i][::3, ::3]; sub_b = B[::3, ::3]; ok = sub_b > 3
            g = float(np.median(sub_i[ok] / sub_b[ok])) if ok.any() else 1.0
            B = B * g; thr = k * sigma * max(1.0, g) ** 0.5
        d = S[i] - B; ad = np.abs(d)
        m = (ad > thr).astype(np.uint8)
        m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, ko), cv2.MORPH_CLOSE, kc)
        nl, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        comps = []
        for j in range(1, nl):
            area = st[j, cv2.CC_STAT_AREA]
            if area < min_area_h2 * hd * hd: continue
            ys, xs = np.nonzero(lab == j)
            w = ad[ys, xs] if core <= 0 else np.maximum(ad[ys, xs] - core * thr / k, 0) + 1e-3
            comps.append((float(w.sum()), area, xs, ys, w))
        comps.sort(key=lambda c: -c[0])
        body = [c for c in comps if c[1] >= body_area_h2 * hd * hd]
        pts = []
        def cen(c): return ((c[2] * c[4]).sum() / c[4].sum(), (c[3] * c[4]).sum() / c[4].sum())
        if len(body) >= n:
            for c in body[:n]: pts.append((*cen(c), c[0], c[1]))
        elif len(body) == 1 and n == 2:
            c = body[0]; cc, labk = wkmeans2(c[2], c[3], c[4])
            for j in range(2): pts.append((cc[j][0], cc[j][1], float(c[4][labk == j].sum()), int((labk == j).sum())))
        elif comps:
            for c in comps[:n]: pts.append((*cen(c), c[0], c[1]))
        P = [(float(px * ds + x0 + ds / 2), float(py * ds + y0 + ds / 2), float(ms), int(ar)) for px, py, ms, ar in pts]
        out.append({'frame': i, 'pts': P, 'nbody': len(body), 'mask': m if keep_masks else None})
    return out, float(sigma)

def support(mask, x0, y0, ds, cx, cy, h, wfrac=0.45):
    """Share of fg pixels inside a person box (centre cx,cy, height h, width wfrac*h) in original px."""
    if mask is None: return 1.0
    bx0 = int((cx - wfrac * h / 2 - x0) / ds); bx1 = int((cx + wfrac * h / 2 - x0) / ds) + 1
    by0 = int((cy - h / 2 - y0) / ds); by1 = int((cy + h / 2 - y0) / ds) + 1
    Hm, Wm = mask.shape
    bx0, bx1, by0, by1 = max(0, bx0), min(Wm, bx1), max(0, by0), min(Hm, by1)
    if bx1 <= bx0 or by1 <= by0: return 0.0
    return float(mask[by0:by1, bx0:bx1].mean())
