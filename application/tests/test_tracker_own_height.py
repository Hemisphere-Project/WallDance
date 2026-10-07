"""Own-height gates (2026-10-07): an established track's re-association gates scale with
min(global person height, the track's own YOLO box height).

The night project's height guard held the walk-in height (427-605 px) while the dancer at the back wall
was ~128 px: a false person 2 body heights from a dancer track bridged for 9 frames sat inside the global
new-track gate, so the unmatched detection was FORCED onto the dancer's track (s6 28.0 s, gamma 1.0).
No GPU, no footage.
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from core.tracker import DancerTrack, DancerTracker  # noqa: E402


# keypoint rows (COCO order) as a fraction of the height above (-) / below (+) the box centre
_PROFILE = [-0.45] * 5 + [-0.30, -0.30, -0.15, -0.15, 0.0, 0.0, 0.0, 0.0, 0.25, 0.25, 0.45, 0.45]


def _det(x, y=400.0, h=120.0, lying=False, reach=False):
    """A detection with a standing skeleton (torso = 0.30 h), or lying (rotated flat), or reaching up
    (wrists far above the head: a taller box, the same torso)."""
    dy = np.array(_PROFILE) * h
    dx = np.array([0, -3, 3, -5, 5, -12, 12, -20, 20, -25, 25, -8, 8, -8, 8, -8, 8], float) * h / 120
    kpts = np.stack([x + (dy if lying else dx), y + (dx if lying else dy)], axis=1)
    if reach:
        kpts[9:11, 1] = y - 0.9 * h
    conf = np.full(17, 0.8)
    xs, ys = kpts[:, 0], kpts[:, 1]
    bbox = np.array([xs.min(), ys.min(), max(xs.max() - xs.min(), 1.0), max(ys.max() - ys.min(), 1.0)])
    return kpts, conf, bbox


def _tracker(person_h, own_gates=True):
    t = DancerTracker()
    t.logger.start_session(tempfile.mkdtemp())
    t.set_person_height(person_h)
    t.own_height_gates = own_gates
    return t


def _establish(t, x=800.0, frames=40, h=120.0):
    for i in range(frames):
        t.update([_det(x, h=h)], frame_number=i)
    assert len(t.tracks) == 1 and t.tracks[0].is_established
    return t.tracks[0]


def test_own_size_comes_from_the_torso_whatever_the_pose():
    k, c, b = _det(300.0)
    trk = DancerTrack(k, c, b)
    for _ in range(18):
        trk.update(k, c, b)
    assert trk.own_height() is None                      # 19 torsos < 20
    trk.update(k, c, b)
    assert abs(trk.own_height() - 0.30 * 120 * 3.4) < 1  # ~ the standing height
    for _ in range(150):                                  # reaching up: a taller box, the same torso
        kk, cc, bb = _det(300.0, reach=True)
        trk.update(kk, cc, bb)
    assert abs(trk.own_height() - 122.4) < 1
    lying = DancerTrack(k, c, b)
    for _ in range(40):                                   # lying on the floor: a flat box, the same torso
        kk, cc, bb = _det(300.0, lying=True)
        lying.update(kk, cc, bb)
    assert abs(lying.own_height() - 122.4) < 1
    blind = DancerTrack(k, np.full(17, 0.2), b)           # no confident shoulders / hips: no own size
    for _ in range(40):
        blind.update(k, np.full(17, 0.2), b)
    assert blind.own_height() is None


def _ghost_next_to_a_tracked_dancer(own_gates):
    """A dancer tracked up to the previous frame (YOLO or the motion bridge), then a false person 2.25 of its
    heights away (s6 28.0 s): the displacement gate (0.5 x the match gate) rejects the pair, and the
    unmatched detection must not be forced onto the dancer's track for being 'too close to be new'."""
    t = _tracker(600, own_gates=own_gates)               # the walk-in height, far too big
    trk = _establish(t)
    t.update([_det(530.0)], frame_number=40)
    return t, trk


def test_a_ghost_next_to_a_dancer_is_not_forced_onto_its_track():
    t, trk = _ghost_next_to_a_tracked_dancer(own_gates=True)
    assert abs(trk.get_last_known_position()[0] - 800.0) < 30   # the dancer's track did not jump
    assert any(abs(x.get_last_known_position()[0] - 530.0) < 30 for x in t.tracks if x is not trk)


def test_without_own_height_gates_the_global_gate_takes_it():
    t, trk = _ghost_next_to_a_tracked_dancer(own_gates=False)
    assert abs(trk.get_last_known_position()[0] - 530.0) < 30   # the old behaviour: the track jumps


def test_a_detection_at_the_dancer_still_matches():
    t = _tracker(600)
    trk = _establish(t)
    t.update([_det(830.0)], frame_number=40)              # 0.25 heights: the dancer moving
    assert abs(trk.get_last_known_position()[0] - 830.0) < 10 and len(t.tracks) == 1


def test_the_gates_never_widen_beyond_the_global_height():
    t = _tracker(100)
    trk = _establish(t, h=200.0)                          # a dancer bigger than the configured height
    assert t._track_match_gate(trk) == t.distance_threshold
    assert t._track_height(trk) == 100.0
    t2 = _tracker(600)
    trk2 = _establish(t2)
    assert abs(t2._track_height(trk2) - 122.4) < 1          # torso 36 px x 3.4
    assert t2._track_match_gate(trk2) < t2.distance_threshold


def test_a_near_ghost_is_not_forced_past_the_displacement_gate():
    """s4 34.2 s: a false person 0.78 heights from a dancer matched the frame before -- inside the
    'too close to be new' radius, but past the track's displacement gate: dropped, not forced."""
    t = _tracker(600)
    trk = _establish(t)
    t.update([_det(800.0 - 94.0)], frame_number=40)
    assert abs(trk.get_last_known_position()[0] - 800.0) < 30
    t_old = _tracker(600, own_gates=False)
    trk_old = _establish(t_old)
    t_old.update([_det(800.0 - 94.0)], frame_number=40)
    assert abs(trk_old.get_last_known_position()[0] - 706.0) < 30     # the global gate let it through


def test_a_well_configured_height_keeps_the_tuned_gates():
    """The own size only takes over when the global height is clearly too big (> 1.5 x): a scene whose
    configured height is about right keeps its tuned gates (the duos moved when it always applied)."""
    t = _tracker(150)                                     # 150 vs an own size of ~122: 1.23 x
    trk = _establish(t)
    assert t._track_height(trk) == 150.0 and t._track_match_gate(trk) == t.distance_threshold
