"""Automatic person height (core/auto_height.py): learned from confident full skeletons."""
from types import SimpleNamespace

import numpy as np

from core.auto_height import AutoHeight


def _st(h, conf=0.9, fss=0):
    kc = np.full(17, conf)
    return SimpleNamespace(frames_since_skeleton=fss, confidence=kc, bbox=np.array([0, 0, 0.4 * h, h]))


def test_learns_the_median_of_confident_full_skeletons():
    ah = AutoHeight(min_samples=10)
    out = None
    for i in range(40):
        out = ah.update([_st(150 + (i % 5)), _st(400, conf=0.2), _st(300, fss=3)], i / 20.0, current=45)
    assert out is not None and 149 <= out <= 155            # unsure / stale skeletons ignored


def test_moves_slowly_after_the_first_estimate_and_forgets_old_samples():
    ah = AutoHeight(min_samples=5, window_s=2.0, rate=0.5)
    for i in range(20):
        first = ah.update([_st(150)], i / 20.0, current=45)
    assert abs(first - 150) < 1
    for i in range(20, 200):                                 # the dancers move closer: 300 px
        out = ah.update([_st(300)], i / 20.0, current=first)
    assert 250 < out <= 300
    assert ah.update([], 100.0, current=out) is None         # nothing recent: keep the current value
