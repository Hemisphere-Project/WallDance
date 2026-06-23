"""P-1 perf-optimization safety: the IDS mono fast path is bit-identical.

The motion feed normally does cv2.cvtColor(BGR2GRAY). P-1 skips that on the
IDS path (mono_raw=True) by taking the single channel, because an IDS mono
frame is expanded to BGR with R==G==B. This proves that equivalence holds for
the full 0-255 range, since goldens (CPU/file source) never exercise the fast
path.
"""
import numpy as np
import cv2


def test_p1_mono_channel_equals_bgr2gray():
    # All 256 gray values, mono expanded to BGR exactly as the IDS path does.
    vals = np.arange(256, dtype=np.uint8).reshape(16, 16)
    bgr = cv2.cvtColor(vals, cv2.COLOR_GRAY2BGR)          # R==G==B
    fast = np.ascontiguousarray(bgr[:, :, 0])             # P-1 fast path
    slow = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)          # the conversion it skips
    assert np.array_equal(fast, slow)                    # bit-for-bit
    assert fast.flags["C_CONTIGUOUS"]                    # safe to hand to MOG2
