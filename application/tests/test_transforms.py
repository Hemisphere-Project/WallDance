"""Coordinate-transform tests (ROADMAP Bug #5, P4).

The ROI -> letterbox -> unscale chain and the crossval tracker -> original ->
ROI-local -> mask transform were correct-as-traced but had zero tests.  P3
Stages 2-3 reroute motion through these transforms, so lock them first.

These exercise the *real* pipeline methods as unbound functions (``self`` is
unused by both), feeding synthetic tracks/masks -- no model or GPU needed.
"""
import types

import numpy as np
import pytest

pytest.importorskip("torch")  # pipeline pulls in torch/ultralytics
from core.pipeline import FrameProcessor  # noqa: E402


def _stub_track(keypoints, bbox, *, velocity=(0.0, 0.0), history=None,
                track_id=7):
    """A minimal duck-typed stand-in for DancerTrack.

    ``_unscale_letterbox`` only reads these attributes/methods and never
    touches ``self``, so we can avoid constructing a full tracker.
    """
    kp = np.asarray(keypoints, dtype=np.float64)
    bb = np.asarray(bbox, dtype=np.float64)
    hist = [np.asarray(h, dtype=np.float64) for h in (history or [])]
    centroid = np.array([bb[0] + bb[2] / 2.0, bb[1] + bb[3] / 2.0])
    return types.SimpleNamespace(
        track_id=track_id,
        keypoints=kp,
        bbox=bb,
        confidence=np.ones(len(kp)),
        history=hist,
        is_bridged=False,
        get_velocity=lambda: np.asarray(velocity, dtype=np.float64),
        get_smoothed_centroid=lambda: centroid,
    )


def _letterbox_forward(pt, scale, pad_x, pad_y, roi_x=0, roi_y=0):
    """Forward of what _unscale_letterbox inverts: original -> letterbox."""
    px, py = pt
    return ((px - roi_x) * scale + pad_x, (py - roi_y) * scale + pad_y)


# ---------------------------------------------------------------------------
# _unscale_letterbox: letterbox/ROI space -> original camera space
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scale,pad_x,pad_y,roi_x,roi_y", [
    (1.0, 0, 0, 0, 0),       # identity
    (0.5, 0, 0, 0, 0),       # pure downscale
    (0.5, 120, 40, 0, 0),    # downscale + letterbox padding
    (0.5, 120, 40, 300, 150),  # downscale + padding + ROI offset
    (0.75, 0, 64, 0, 0),     # vertical-only pad (wide source)
    (1.0, 0, 280, 0, 0),     # bug #9 regime: no scaling, one-axis pad
])
def test_unscale_letterbox_inverts_forward(scale, pad_x, pad_y, roi_x, roi_y):
    # Known points in ORIGINAL camera space.
    orig_pts = np.array([[400.0, 820.0], [873.5, 564.0], [10.0, 10.0]])
    # Project forward into letterbox space and build a track there.
    lb_kpts = np.array([_letterbox_forward(p, scale, pad_x, pad_y, roi_x, roi_y)
                        for p in orig_pts])
    # Original bbox -> letterbox bbox (x,y shift+scale; w,h scale only).
    obx, oby, obw, obh = 380.0, 600.0, 60.0, 200.0
    lb_bbox = [(obx - roi_x) * scale + pad_x, (oby - roi_y) * scale + pad_y,
               obw * scale, obh * scale]

    trk = _stub_track(lb_kpts, lb_bbox, velocity=(2.0 * scale, -3.0 * scale))
    out = FrameProcessor._unscale_letterbox(
        None, trk, scale, pad_x, pad_y, roi_x, roi_y)

    np.testing.assert_allclose(out.keypoints, orig_pts, atol=1e-6)
    np.testing.assert_allclose(out.bbox, [obx, oby, obw, obh], atol=1e-6)
    # Velocity carries inverse scale only (no padding/offset).
    np.testing.assert_allclose(out.velocity, [2.0, -3.0], atol=1e-6)
    np.testing.assert_allclose(
        out.smoothed_centroid,
        [obx + obw / 2.0, oby + obh / 2.0], atol=1e-6)


def test_unscale_letterbox_history_roundtrip():
    scale, pad_x, pad_y, roi_x, roi_y = 0.5, 100, 20, 200, 50
    orig_hist = [(300.0, 400.0), (310.0, 405.0)]
    lb_hist = [_letterbox_forward(p, scale, pad_x, pad_y, roi_x, roi_y)
               for p in orig_hist]
    trk = _stub_track([[0.0, 0.0]], [0.0, 0.0, 10.0, 10.0], history=lb_hist)
    out = FrameProcessor._unscale_letterbox(
        None, trk, scale, pad_x, pad_y, roi_x, roi_y)
    np.testing.assert_allclose(out.history, orig_hist, atol=1e-6)


def test_unscale_letterbox_nonfinite_velocity_is_zeroed():
    trk = _stub_track([[0.0, 0.0]], [0.0, 0.0, 10.0, 10.0],
                      velocity=(np.inf, np.nan))
    out = FrameProcessor._unscale_letterbox(None, trk, 0.5, 0, 0)
    np.testing.assert_array_equal(out.velocity, [0.0, 0.0])


# ---------------------------------------------------------------------------
# _exclusion_norm_xy: tracker centroid -> normalized [0,1] mask coords
# (mirrors the inline crossval tracker->original->ROI-local->mask transform)
# ---------------------------------------------------------------------------
def _stub_motion_det(mask_w, mask_h, mog_scale):
    """Duck-typed MotionDetector exposing only what the transform reads."""
    return types.SimpleNamespace(
        _clean_mask=np.zeros((mask_h, mask_w), dtype=np.uint8),
        _scale=mog_scale,
    )


def _crossval_mask_xy(cx, cy, scale, pad_x, pad_y, roi_x, roi_y,
                      roi_local, mog_scale, mw, mh):
    """Replicates the inline crossval transform in _crossval_motion_filter,
    expressed as normalized mask coords -- the spec _exclusion_norm_xy must match.

    Pad is subtracted unconditionally: scale == 1.0 can still carry a one-axis
    letterbox pad (bug #9; the old ``if scale != 1.0`` guard dropped it).
    """
    inv = 1.0 / scale if scale > 0 else 1.0
    ox = (cx - pad_x) * inv
    oy = (cy - pad_y) * inv
    if roi_local:
        mask_x, mask_y = ox, oy
    else:
        mask_x, mask_y = ox - roi_x, oy - roi_y
    return (mask_x * mog_scale) / mw, (mask_y * mog_scale) / mh


@pytest.mark.parametrize("scale,pad_x,pad_y,roi_x,roi_y,roi_local", [
    (1.0, 0, 0, 0, 0, False),
    (0.5, 120, 40, 0, 0, False),
    (0.5, 120, 40, 300, 150, False),   # CPU path: subtract ROI offset
    (0.5, 120, 40, 300, 150, True),    # GPU path: roi already local
    (1.0, 0, 280, 0, 0, True),         # bug #9: scale 1, one-axis pad (1280x720 @ 1280)
    (1.0, 160, 0, 300, 150, False),    # bug #9: scale 1, x pad + ROI offset
])
def test_exclusion_norm_xy_matches_crossval_transform(
        scale, pad_x, pad_y, roi_x, roi_y, roi_local):
    mw, mh, mog_scale = 160, 100, 0.5
    md = _stub_motion_det(mw, mh, mog_scale)
    cx, cy = 540.0, 430.0
    params = (scale, pad_x, pad_y, roi_x, roi_y, roi_local)
    got = FrameProcessor._exclusion_norm_xy(None, cx, cy, md, *params)
    expected = _crossval_mask_xy(cx, cy, scale, pad_x, pad_y, roi_x, roi_y,
                                 roi_local, mog_scale, mw, mh)
    assert got is not None
    np.testing.assert_allclose(got, expected, atol=1e-9)


def test_exclusion_norm_xy_none_without_mask():
    md = types.SimpleNamespace(_clean_mask=None, _scale=0.5)
    out = FrameProcessor._exclusion_norm_xy(
        None, 100.0, 100.0, md, 1.0, 0, 0, 0, 0, False)
    assert out is None


def test_exclusion_norm_xy_subtracts_pad_at_scale_one():
    """Bug #9 ground truth: a 1280x720 ROI at imgsz 1280 letterboxes with
    scale 1.0 and pad_y 280 -- the pad must still be subtracted (the old
    ``if scale != 1.0`` guard skipped it and sampled the mask 280 px off)."""
    mw, mh, mog_scale = 640, 360, 0.5
    md = _stub_motion_det(mw, mh, mog_scale)
    # Dancer at ROI-local (600, 400) appears at letterbox y = 400 + 280.
    nx, ny = FrameProcessor._exclusion_norm_xy(
        None, 600.0, 400.0 + 280.0, md, 1.0, 0, 280, 0, 0, True)
    np.testing.assert_allclose(
        (nx, ny), ((600.0 * mog_scale) / mw, (400.0 * mog_scale) / mh),
        atol=1e-9)


def test_exclusion_norm_xy_roi_local_vs_global_differ_by_offset():
    """The GPU (roi_local) and CPU (global) paths must differ by exactly the
    ROI offset mapped into normalized mask space -- the crux of Bug #5."""
    mw, mh, mog_scale, scale = 160, 100, 0.5, 0.5
    md = _stub_motion_det(mw, mh, mog_scale)
    cx, cy, roi_x, roi_y = 540.0, 430.0, 300, 150
    nx_local, ny_local = FrameProcessor._exclusion_norm_xy(
        None, cx, cy, md, scale, 120, 40, roi_x, roi_y, True)
    nx_glob, ny_glob = FrameProcessor._exclusion_norm_xy(
        None, cx, cy, md, scale, 120, 40, roi_x, roi_y, False)
    # global subtracts roi before scaling to mask: offset = roi * mog_scale / mw
    np.testing.assert_allclose(nx_local - nx_glob, roi_x * mog_scale / mw, atol=1e-9)
    np.testing.assert_allclose(ny_local - ny_glob, roi_y * mog_scale / mh, atol=1e-9)


# ---------------------------------------------------------------------------
# Box-conf map (⑤a): the per-frame bbox→conf value key.  (Track P removed the
# CPU ROI-offset path; the GPU path keys box confs in letterbox space directly.)
# ---------------------------------------------------------------------------
def test_box_conf_key_recovers_track_match():
    # DancerTrack.update stores the matched bbox verbatim (np.array(bbox)),
    # so the value key on the track side equals the detection-side key.
    key_fn = FrameProcessor._bbox_conf_key
    det_bbox = np.array([101.30001, 220.7, 55.2, 180.9], dtype=np.float32)
    track_bbox = np.array(det_bbox)   # what update() stores
    assert key_fn(det_bbox) == key_fn(track_bbox)
