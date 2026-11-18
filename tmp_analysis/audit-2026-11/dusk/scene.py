"""Synthetic wall scene for the dusk-light-drift audit (throwaway, CPU only).

Raw 8-bit mono frame = K * R(x, y) * (a_ir * E_ir(x, y) + a_day * E_day(x, y)) + sensor noise.
  R      wall reflectance: low-frequency patches, fine texture, mortar lines, a brighter door
  E_ir   two projector hot spots + a floor (constant during the show)
  E_day  daylight: vertical gradient (fades during the show)
Calibrate drives the ROI median to 70 DN (AUTOCAL_SERVO_TARGET_BRIGHTNESS), so K is set from that at t = 0.
Noise: sigma^2 = read^2 + c * signal (shot + read), then rounded and clipped to uint8.
Dancers: a body silhouette (head, torso, legs) of reflectance rho x the same illumination as the wall.
"""
import math

import cv2
import numpy as np

W, H = 1280, 560            # the ROI = the whole frame here (multiple of 4)
PH = 130                    # dancer height in px (night bodies: median 127 px at the back wall, PLAN_25M C.13)


def _smooth(rng, sigma):
    f = rng.normal(0, 1, (H, W)).astype(np.float32)
    f = cv2.GaussianBlur(f, (0, 0), sigma)
    return f / max(1e-6, float(f.std()))


class Scene:
    def __init__(self, seed=0, day_frac=0.7, read=1.2, sigma70=2.5, texture=1.0):
        """day_frac: share of the wall light that is daylight at Calibrate time (0.7 -> the night is 0.3x)."""
        rng = np.random.default_rng(seed)
        R = 1.0 + texture * (0.18 * _smooth(rng, 60) + 0.07 * _smooth(rng, 2.0))
        R[::40, :] *= 0.85
        R[:, ::80] *= 0.85
        R[300:, 150:300] *= 1.6            # a lit roll-up door
        self.R = np.clip(R, 0.2, 3.0).astype(np.float32)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        e = 0.25 + np.exp(-(((xx - 400) / 260) ** 2 + ((yy - 280) / 200) ** 2)) \
            + np.exp(-(((xx - 880) / 260) ** 2 + ((yy - 280) / 200) ** 2))
        self.E_ir = (e / np.median(e)).astype(np.float32)
        d = 1.25 - 0.5 * yy / H
        self.E_day = (d / np.median(d)).astype(np.float32)
        self.day_frac = float(day_frac)
        self.a_ir = 1.0 - self.day_frac
        base = self.R * (self.a_ir * self.E_ir + self.day_frac * self.E_day)
        self.K = 70.0 / float(np.median(base))
        self.read = float(read)
        self.c = max(0.0, (sigma70 ** 2 - read ** 2) / 70.0)
        self.rng = np.random.default_rng(seed + 1000)
        self.yy, self.xx = yy, xx

    # ---- illumination -------------------------------------------------
    def illum(self, day=1.0, gain=1.0):
        """day: remaining share of the calibration-time daylight (1 -> 0); gain: extra global factor."""
        return gain * (self.a_ir * self.E_ir + self.day_frac * day * self.E_day)

    def clean(self, day=1.0, gain=1.0, dancers=()):
        """Noise-free signal (float32).  dancers: (cx, cy, rho[, h])."""
        ill = self.illum(day, gain)
        s = self.K * self.R * ill
        for d in dancers:
            cx, cy, rho = d[0], d[1], d[2]
            h = d[3] if len(d) > 3 else PH
            m = body_mask(cx, cy, h)
            s = np.where(m, self.K * rho * ill, s)
        return s.astype(np.float32)

    def frame(self, day=1.0, gain=1.0, dancers=()):
        s = self.clean(day, gain, dancers)
        sig = np.sqrt(self.read ** 2 + self.c * s)
        f = s + self.rng.standard_normal(s.shape, dtype=np.float32) * sig
        return np.clip(np.rint(f), 0, 255).astype(np.uint8)

    def light_ratio_true(self, day=1.0, gain=1.0):
        return float(np.median(self.illum(day, gain) / self.illum(1.0, 1.0)))


_mask_cache = {}


def body_mask(cx, cy, h=PH):
    """Boolean (H, W) body silhouette centred on (cx, cy), height h: fills ~40 % of the slot layer's
    support box (0.45 h x h)."""
    key = (int(round(cx)), int(round(cy)), int(round(h)))
    m = _mask_cache.get(key)
    if m is not None:
        return m
    m = np.zeros((H, W), np.uint8)
    cx, cy = key[0], key[1]
    cv2.circle(m, (cx, int(cy - 0.43 * h)), max(2, int(0.07 * h)), 1, -1)
    cv2.rectangle(m, (int(cx - 0.12 * h), int(cy - 0.36 * h)), (int(cx + 0.12 * h), int(cy + 0.05 * h)), 1, -1)
    cv2.rectangle(m, (int(cx - 0.10 * h), int(cy + 0.05 * h)), (int(cx - 0.02 * h), int(cy + 0.5 * h)), 1, -1)
    cv2.rectangle(m, (int(cx + 0.02 * h), int(cy + 0.05 * h)), (int(cx + 0.10 * h), int(cy + 0.5 * h)), 1, -1)
    m = m.astype(bool)
    if len(_mask_cache) > 4000:
        _mask_cache.clear()
    _mask_cache[key] = m
    return m


def gamma_lut(gamma):
    inv = 1.0 / gamma
    return np.array([((i / 255.0) ** inv) * 255 for i in range(256)], dtype=np.uint8)
