"""Output box-clamp tests (OPERATOR_V2 Track X, OSC_CONTRACT §B.1).

The box-clamp reports a last-known-YOLO-size box at the smoothed centroid while
a track is sustained without a fresh YOLO skeleton, fixing the case-1 /bbox
size flicker.  It is OUTPUT-ONLY: it must NEVER mutate ``DancerTrack.bbox``
(which drives the bridge gate + MAX_VELOCITY).  These tests exercise the
per-track ``reported_bbox`` plus both pipeline finalize paths
(``_identity_scaled_track`` CPU, ``_unscale_letterbox`` GPU).
"""
import numpy as np
import pytest

pytest.importorskip("torch")  # pipeline pulls in torch/ultralytics
from core.tracker import DancerTrack  # noqa: E402
from core.pipeline import FrameProcessor  # noqa: E402


def _skeleton(conf=0.9):
    """17 keypoints all at (100, 200) so the weighted centroid is (100, 200)."""
    kp = np.tile([100.0, 200.0], (17, 1)).astype(np.float64)
    c = np.full(17, float(conf))
    return kp, c


def _make_track(bbox):
    kp, c = _skeleton(0.9)  # born from a real YOLO skeleton
    return DancerTrack(kp, c, np.array(bbox, dtype=np.float64))


# ---------------------------------------------------------------------------
# reported_bbox semantics
# ---------------------------------------------------------------------------
def test_fresh_skeleton_reports_raw_bbox_even_when_clamp_on():
    """A track with a fresh YOLO skeleton this frame (_frames_since_skeleton==0)
    is never clamped — the raw YOLO box is the ground truth."""
    t = _make_track([10, 20, 50, 80])
    assert t._frames_since_skeleton == 0
    np.testing.assert_allclose(t.reported_bbox(True), [10, 20, 50, 80])
    np.testing.assert_allclose(t.reported_bbox(False), [10, 20, 50, 80])


def test_gap_frame_clamps_size_to_last_yolo_at_smoothed_centroid():
    """During a gap (no fresh skeleton) the reported box is the last-YOLO size
    centered on the smoothed centroid; raw extent only when clamp is off."""
    t = _make_track([10, 20, 50, 80])  # last_yolo_wh=(50,80), centroid=(100,200)
    t.predict()                        # advance a frame: _frames_since_skeleton -> 1
    assert t._frames_since_skeleton > 0
    # clamp ON: box of (50,80) centered at (100,200) -> top-left (75,160)
    np.testing.assert_allclose(t.reported_bbox(True), [75, 160, 50, 80])
    # clamp OFF: raw bbox (predict never touches bbox)
    np.testing.assert_allclose(t.reported_bbox(False), [10, 20, 50, 80])


def test_reported_bbox_never_mutates_internal_bbox():
    """The case-1 invariant: reading the reported box leaves self.bbox intact."""
    t = _make_track([10, 20, 50, 80])
    t.predict()
    _ = t.reported_bbox(True)
    _ = t.reported_bbox(False)
    np.testing.assert_allclose(t.bbox, [10, 20, 50, 80])


def test_last_yolo_size_refreshes_on_real_skeleton_not_cold_blob():
    """The size memory tracks real YOLO boxes and ignores cold-blob extents."""
    t = _make_track([10, 20, 50, 80])
    np.testing.assert_allclose(t._last_yolo_wh, [50, 80])

    # A new real skeleton refreshes the size memory.
    kp, c = _skeleton(0.9)
    t.update(kp, c, np.array([0, 0, 60, 90], dtype=np.float64))
    np.testing.assert_allclose(t._last_yolo_wh, [60, 90])

    # A cold-blob frame (predict then a zero-confidence "detection" with a fat
    # extent) must NOT poison the size memory — the flicker source.
    t.predict()
    cold_kp = np.tile([100.0, 200.0], (17, 1)).astype(np.float64)
    cold_c = np.zeros(17)
    t.update(cold_kp, cold_c, np.array([0, 0, 200, 300], dtype=np.float64))
    assert t._frames_since_skeleton > 0          # cold blob is not a skeleton
    np.testing.assert_allclose(t._last_yolo_wh, [60, 90])  # unchanged


# ---------------------------------------------------------------------------
# Pipeline finalize wiring (CPU identity + GPU letterbox), self=None like the
# transform tests (the finalize methods never touch self for the clamp).
# ---------------------------------------------------------------------------
def test_cpu_finalize_applies_clamp_flag():
    t = _make_track([10, 20, 50, 80])
    t.predict()  # gap frame
    on = FrameProcessor._identity_scaled_track(t, True)
    off = FrameProcessor._identity_scaled_track(t, False)
    np.testing.assert_allclose(on.bbox, [75, 160, 50, 80])
    np.testing.assert_allclose(off.bbox, [10, 20, 50, 80])
    np.testing.assert_allclose(t.bbox, [10, 20, 50, 80])  # not mutated


def test_gpu_finalize_clamps_in_tracker_space_then_unscales():
    """clamp happens in tracker space, so the letterbox unscale applies to the
    clamped box just like the raw one (identity transform here)."""
    t = _make_track([10, 20, 50, 80])
    t.predict()
    on = FrameProcessor._unscale_letterbox(None, t, 1.0, 0, 0, 0, 0, True)
    off = FrameProcessor._unscale_letterbox(None, t, 1.0, 0, 0, 0, 0, False)
    np.testing.assert_allclose(on.bbox, [75, 160, 50, 80])
    np.testing.assert_allclose(off.bbox, [10, 20, 50, 80])

    # Under a real downscale the clamped w/h scale by inv_scale too.
    on_half = FrameProcessor._unscale_letterbox(None, t, 0.5, 0, 0, 0, 0, True)
    np.testing.assert_allclose(on_half.bbox, [150, 320, 100, 160])
