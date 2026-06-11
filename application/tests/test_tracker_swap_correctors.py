"""Unit tests for the swap-corrector master switch (ROADMAP §4.2 Phase 2 ⑧, §3a).

The three post-hoc swap correctors (occlusion-cascade, merge-direction,
two-opt) are gated behind one instance switch ``tracker.swap_correctors``
(config default ``TRACKER_SWAP_CORRECTORS``, per-scene replay key
``tracker_swap_correctors``).  These tests pin the gating contract only —
the correctors' internals are corpus-measured, not unit-tested.

Drives real ``DancerTrack`` objects through ``update()`` — no GPU.
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

from config import TRACKER_SWAP_CORRECTORS  # noqa: E402
from tracker import DancerTrack, DancerTracker  # noqa: E402

CORRECTORS = ("_check_occlusion_cascade_swaps",
              "_check_merge_direction_swaps",
              "_check_two_opt_swaps")


def _mk_track(x: float, n_matches: int = 30):
    kpts = np.tile([x + 30.0, 190.0], (17, 1)).astype(float)
    conf = np.full(17, 0.8)
    t = DancerTrack(kpts, conf, bbox=np.array([x, 100.0, 60.0, 180.0]))
    for _ in range(n_matches - 1):
        t.predict()
        t.update(kpts, conf, np.array([x, 100.0, 60.0, 180.0]))
    assert t.is_established
    return t


def _mk_tracker():
    tk = DancerTracker()
    tk.logger.start_session(tempfile.mkdtemp())
    tk._person_height_px = 180
    tk.frame_count = 10_000
    tk.tracks = [_mk_track(100.0), _mk_track(400.0)]
    return tk


def _spy_correctors(tk):
    """Replace the corrector methods with call recorders."""
    calls = []
    for name in CORRECTORS:
        setattr(tk, name, lambda *a, _n=name, **k: calls.append(_n))
    return calls


def _dets_for(tk):
    """One matching detection per existing track."""
    dets = []
    for t in tk.tracks:
        x = float(t.bbox[0])
        kpts = np.tile([x + 30.0, 190.0], (17, 1)).astype(float)
        dets.append((kpts, np.full(17, 0.8),
                     np.array([x, 100.0, 60.0, 180.0])))
    return dets


def test_default_matches_config():
    assert DancerTracker().swap_correctors is TRACKER_SWAP_CORRECTORS


def test_switch_survives_reset():
    tk = DancerTracker()
    tk.swap_correctors = not TRACKER_SWAP_CORRECTORS
    tk.reset()
    assert tk.swap_correctors is (not TRACKER_SWAP_CORRECTORS)


def test_correctors_skipped_when_off():
    tk = _mk_tracker()
    tk.swap_correctors = False
    calls = _spy_correctors(tk)
    tk.update(_dets_for(tk))
    assert calls == []


def test_correctors_invoked_when_on():
    tk = _mk_tracker()
    tk.swap_correctors = True
    calls = _spy_correctors(tk)
    tk.update(_dets_for(tk))
    assert set(calls) == set(CORRECTORS)
