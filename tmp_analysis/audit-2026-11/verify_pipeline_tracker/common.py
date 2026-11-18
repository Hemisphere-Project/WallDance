"""Shared fakes for the tracker repros (no GPU, no repo writes)."""
import os, sys, tempfile
import numpy as np
sys.path.insert(0, "/data/WallDance/application/src")
import core.config as config
from core.tracker import DancerTracker, DancerTrack

H = 120.0   # person height in tracker px

REL = np.array([
    [0.00, -0.42], [0.02, -0.44], [-0.02, -0.44], [0.04, -0.43], [-0.04, -0.43],
    [0.11, -0.30], [-0.11, -0.30], [0.14, -0.14], [-0.14, -0.14], [0.15, 0.00], [-0.15, 0.00],
    [0.08, 0.03], [-0.08, 0.03], [0.08, 0.25], [-0.08, 0.25], [0.08, 0.47], [-0.08, 0.47]])


def det(cx, cy, h=H, conf=0.9):
    k = REL * h + np.array([cx, cy], dtype=np.float64)
    c = np.full(17, float(conf))
    b = np.array([cx - 0.175 * h, cy - h / 2.0, 0.35 * h, h], dtype=np.float64)
    return (k, c, b)


def synth(cx, cy, w=40.0, h=H):
    """A cold-blob synthetic detection as _fuse_motion_blobs builds it."""
    k = np.full((17, 2), [cx, cy], dtype=np.float64)
    return (k, np.zeros(17), np.array([cx - w / 2, cy - h / 2, w, h], dtype=np.float64))


def kcent(d):
    k, c, _ = d
    return np.average(k, axis=0, weights=c)


def mk():
    t = DancerTracker()
    t.logger.start_session(tempfile.mkdtemp())
    t.reset()
    t.set_person_height(int(H))
    t.set_frame_dimensions(1280, pad_x=0)
    return t


def ev(t, frame, skip=("FRAME_SUMMARY",)):
    return [(e["event"], e.get("data")) for e in t.logger._buffer
            if e.get("frame") == frame and e["event"] not in skip]


def names(t, frame):
    return [e for e, _ in ev(t, frame)]


def state(t):
    return [(tr.track_id, [round(float(v), 1) for v in tr.get_centroid()], "hits", tr.hits,
             "tsu", tr.time_since_update, "wu", round(tr._warmup_score, 1),
             "occ", tr._occluded, "fss", tr._frames_since_skeleton) for tr in t.tracks]
