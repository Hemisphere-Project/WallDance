"""Output box-size smoothing tests (OPERATOR_V2 Track X, OSC_CONTRACT §B.2).

Causal box-SIZE EMA on the reported boxes: output-only, smooths the bbox size
around its own center, alpha = BOX_SIZE_OUTPUT_SMOOTHING / L.  Exercised via the
unbound FrameProcessor method with a duck-typed self (it only reads
self.settings.output_smoothing_l and self._box_size_ema), mirroring the
transform tests' self=None style.
"""
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("torch")  # pipeline pulls in torch/ultralytics
from pipeline import FrameProcessor, ScaledTrack  # noqa: E402
from core.config import BOX_SIZE_OUTPUT_SMOOTHING  # noqa: E402


def _fake(L):
    return SimpleNamespace(settings=SimpleNamespace(output_smoothing_l=L),
                           _box_size_ema={})


def _st(tid, bbox):
    return ScaledTrack(
        track_id=tid,
        keypoints=np.zeros((17, 2)),
        confidence=np.zeros(17),
        bbox=np.array(bbox, dtype=np.float64),
        history=[],
        velocity=np.zeros(2),
    )


def test_first_frame_seeds_ema_unchanged():
    fake = _fake(1)
    st = _st(1, [0, 0, 100, 100])
    FrameProcessor._smooth_output_box_sizes(fake, [st])
    np.testing.assert_allclose(st.bbox, [0, 0, 100, 100])
    assert fake._box_size_ema[1] == (100.0, 100.0)


def test_l1_alpha_half_smooths_size_around_center():
    fake = _fake(1)  # alpha = 0.5
    FrameProcessor._smooth_output_box_sizes(fake, [_st(1, [0, 0, 100, 100])])
    st = _st(1, [0, 0, 200, 200])      # center (100,100), new size 200
    FrameProcessor._smooth_output_box_sizes(fake, [st])
    # smoothed size = 0.5*200 + 0.5*100 = 150, re-centered on (100,100)
    np.testing.assert_allclose(st.bbox, [25, 25, 150, 150])


def test_higher_L_smooths_more_slowly():
    base = BOX_SIZE_OUTPUT_SMOOTHING
    # L=1 vs L=3 over the same step: L=3 (smaller alpha) moves less.
    f1, f3 = _fake(1), _fake(3)
    for f in (f1, f3):
        FrameProcessor._smooth_output_box_sizes(f, [_st(1, [0, 0, 100, 100])])
    s1, s3 = _st(1, [0, 0, 200, 200]), _st(1, [0, 0, 200, 200])
    FrameProcessor._smooth_output_box_sizes(f1, [s1])
    FrameProcessor._smooth_output_box_sizes(f3, [s3])
    assert s1.bbox[2] > s3.bbox[2]                      # L=1 jumped further
    np.testing.assert_allclose(s1.bbox[2], 100 + base * 100)        # alpha=0.5
    np.testing.assert_allclose(s3.bbox[2], 100 + (base / 3) * 100)  # alpha=1/6


def test_dead_tracks_are_pruned():
    fake = _fake(1)
    FrameProcessor._smooth_output_box_sizes(
        fake, [_st(1, [0, 0, 100, 100]), _st(2, [0, 0, 50, 50])])
    assert set(fake._box_size_ema) == {1, 2}
    # next frame only reports track 1 -> track 2's state is pruned
    FrameProcessor._smooth_output_box_sizes(fake, [_st(1, [0, 0, 100, 100])])
    assert set(fake._box_size_ema) == {1}
