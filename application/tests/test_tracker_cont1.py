"""CONT-1 tracker bug fixes (audit 2026-10 ``01-continuity.md`` §6).

* BUG-1  resurrect restores warm-up ONLY for previously emitted ids.
* BUG-2  fractional occlusion aging never drives time_since_update below 0.
* BUG-3  replay applies tracker_max_age AFTER set_tracking_mode (app order).
* BUG-4  tracker_intermittent_confirm + tracker_ghost_skeleton_age are in the
         known-N search space, and the new key drives the frozen-ghost gate.

No GPU, no footage (BUG-3 builds a model-less processor in a subprocess,
because importing ``replay`` re-execs the interpreter for the CUDA libs).
"""
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))

import core.config as config  # noqa: E402
from core import config_schema  # noqa: E402
from core.tracker import (  # noqa: E402
    DancerTrack, DancerTracker, DormantSnapshot, FrameUpdateContext)


def _det(x=300.0, y=200.0, h=180.0):
    kpts = np.tile([x, y], (17, 1)).astype(float)
    conf = np.full(17, 0.8)
    bbox = np.array([x - 30.0, y - h / 2, 60.0, h])
    return kpts, conf, bbox


def _tracker():
    t = DancerTracker()
    t.logger.start_session(tempfile.mkdtemp())
    t.set_person_height(180)
    t.frame_count = 1000
    return t


def _dormant_track(t, *, reported, hits=40):
    """A real track that went dormant at (300, 200) after ``hits`` matches."""
    k, c, b = _det()
    trk = DancerTrack(k, c, b)
    for _ in range(hits):
        trk.predict()
        trk.update(k, c, b)
    trk._ever_reported = reported
    t._dormant.append(DormantSnapshot(trk, exited_from_edge=False))
    return trk.track_id


# --------------------------------------------------------------------------- #
# BUG-1
# --------------------------------------------------------------------------- #
def test_snapshot_records_whether_the_id_was_emitted():
    k, c, b = _det()
    trk = DancerTrack(k, c, b)
    assert DormantSnapshot(trk).was_reported is False
    trk._ever_reported = True
    assert DormantSnapshot(trk).was_reported is True


def test_resurrect_restores_warmup_for_a_previously_emitted_id():
    t = _tracker()
    tid = _dormant_track(t, reported=True)
    k, c, b = _det(x=305.0)
    new = t._try_resurrect(k, c, b, np.array([305.0, 200.0]), FrameUpdateContext())
    assert new is not None and new.track_id == tid
    assert new._warmup_score >= config.TRACK_WARMUP_THRESHOLD
    assert new._ever_reported
    t.tracks = [new]
    assert [x.track_id for x in t._collect_confirmed_tracks()] == [tid]


def test_resurrect_keeps_fresh_warmup_for_a_never_emitted_id():
    """The guard that keeps BUG-1's fix from re-emitting scenery ghosts."""
    t = _tracker()
    tid = _dormant_track(t, reported=False)
    k, c, b = _det(x=305.0)
    new = t._try_resurrect(k, c, b, np.array([305.0, 200.0]), FrameUpdateContext())
    assert new is not None and new.track_id == tid
    assert new._warmup_score == 1.0
    assert new.hits >= 40                      # hits still restored, as before
    t.tracks = [new]
    assert t._collect_confirmed_tracks() == []


def test_collect_marks_only_emitted_tracks():
    t = _tracker()
    k, c, b = _det()
    shown = DancerTrack(k, c, b)
    shown.hits, shown._warmup_score = 30, config.TRACK_WARMUP_THRESHOLD
    hidden = DancerTrack(*_det(x=900.0))
    hidden.hits, hidden._warmup_score = 30, 3.0
    t.tracks = [shown, hidden]
    t._collect_confirmed_tracks()
    assert shown._ever_reported and not hidden._ever_reported


# --------------------------------------------------------------------------- #
# BUG-2
# --------------------------------------------------------------------------- #
def test_occlusion_aging_never_goes_negative_on_a_bridged_track():
    t = _tracker()
    trk = DancerTrack(*_det())
    trk.time_since_update = 0          # a motion bridge reset it this frame
    t._apply_fractional_occlusion_aging(trk)
    assert trk.time_since_update == 0  # was -1 before the fix


def test_occlusion_aging_still_slows_an_unbridged_track():
    t = _tracker()
    trk = DancerTrack(*_det())
    trk._fractional_age = 0.0
    seen = []
    for _ in range(12):
        trk.time_since_update += 1     # predict() increment of a miss
        t._apply_fractional_occlusion_aging(trk)
        seen.append(trk.time_since_update)
    # predict's +1 is undone every frame; a whole frame accrues every
    # 1/TRACKER_OCCLUSION_AGE_FACTOR frames (0.1 -> 10)
    assert min(seen) >= 0
    assert seen[-1] == int(12 * config.TRACKER_OCCLUSION_AGE_FACTOR + 1e-9)


# --------------------------------------------------------------------------- #
# Follow-up to BUG-2: occluded-duplicate bridge warm-up guard
# --------------------------------------------------------------------------- #
class _BlobAtQuery:
    """Fake motion detector: a big local blob right at the queried centroid."""

    def extract_local_motion_blob(self, qx, qy, qw, qh, target_centroid=None,
                                  min_motion_ratio=0.0, include_shadows=False):
        from types import SimpleNamespace
        c = np.asarray(target_centroid, dtype=float).copy()
        return SimpleNamespace(centroid=c, area=5000.0), 0.5


def _bridge_scene():
    """Track 0 matched this frame at x=300; track 1 unmatched 20 px from it
    (inside the occlusion radius); track 2 unmatched 600 px away."""
    t = _tracker()
    matched = DancerTrack(*_det(x=300.0))
    near = DancerTrack(*_det(x=320.0))
    far = DancerTrack(*_det(x=900.0))
    for trk in (near, far):
        trk.hits = 30                     # established -> bridge-eligible
        trk._warmup_score = 5.0
        trk.time_since_update = 1         # missed this frame
    t.tracks = [matched, near, far]
    return t, near, far


def test_bridge_gives_no_warmup_credit_next_to_a_matched_track():
    t, near, far = _bridge_scene()
    t._lazy_bridge_with_motion(_BlobAtQuery(), matched_trk={0})
    # both were relayed by the bridge ...
    assert near.is_bridged and far.is_bridged
    assert near.time_since_update == 0 and far.time_since_update == 0
    # ... but only the free-standing one earned the +0.4 integral credit
    assert near._warmup_score == 5.0
    assert far._warmup_score == pytest.approx(5.0 + config.MOTION_BRIDGE_WARMUP_INCREMENT)


def test_bridge_warmup_guard_switch(monkeypatch):
    import core.tracker as T
    monkeypatch.setattr(T, "MOTION_BRIDGE_OCCLUDED_WARMUP_GUARD", False)
    t, near, _far = _bridge_scene()
    t._lazy_bridge_with_motion(_BlobAtQuery(), matched_trk={0})
    assert near._warmup_score == pytest.approx(5.0 + config.MOTION_BRIDGE_WARMUP_INCREMENT)
    assert config.MOTION_BRIDGE_OCCLUDED_WARMUP_GUARD is True     # shipped default


# --------------------------------------------------------------------------- #
# BUG-4 (config key + search space)
# --------------------------------------------------------------------------- #
class _Frozen:
    """Confirmed, skeleton-stale for 10 frames, not moving."""
    track_id = 7
    hits = 50
    _warmup_score = config.TRACK_WARMUP_THRESHOLD
    warmup_confirmed = True
    bbox = np.array([0.0, 0.0, 60.0, 180.0])
    _frames_since_skeleton = 10

    def get_velocity(self):
        return np.zeros(2)


def test_ghost_skeleton_age_defaults_to_the_shipped_constant():
    assert DancerTracker().ghost_skeleton_age == config.TRACKER_GHOST_SKELETON_AGE


def test_ghost_skeleton_age_drives_the_frozen_gate():
    t = _tracker()
    t.tracks = [_Frozen()]
    assert t._collect_confirmed_tracks() == []          # default 3 < 10 -> frozen
    t.ghost_skeleton_age = 15
    t.tracks = [_Frozen()]
    assert [x.track_id for x in t._collect_confirmed_tracks()] == [7]


def test_known_n_searches_the_report_gate_knobs():
    import known_n
    space = known_n.KNOWN_N_SPACE
    assert space["tracker_intermittent_confirm"] == [False, True]
    assert config.TRACKER_GHOST_SKELETON_AGE in space["tracker_ghost_skeleton_age"]
    assert 15 in space["tracker_ghost_skeleton_age"]   # the audit's combo value


def test_ghost_skeleton_age_is_clamped_on_load():
    flat, warns = config_schema.validate_flat({"tracker_ghost_skeleton_age": 0})
    assert flat["tracker_ghost_skeleton_age"] == 1 and warns
    flat, _ = config_schema.validate_flat({"tracker_ghost_skeleton_age": 15})
    assert flat["tracker_ghost_skeleton_age"] == 15


# --------------------------------------------------------------------------- #
# BUG-3
# --------------------------------------------------------------------------- #
_PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
import replay
out = {}
for mode in ("motion_first", "yolo_first"):
    p = replay._build_processor(
        {"tracking_mode": mode, "tracker_max_age": 15,
         "tracker_ghost_skeleton_age": 9, "tracker_intermittent_confirm": True},
        "yolo11x-pose", 1280, load_model=False)
    out[mode] = [p.tracker.max_age, p.tracker.ghost_skeleton_age,
                 p.tracker.intermittent_confirm]
p = replay._build_processor({"tracking_mode": "motion_first"}, "yolo11x-pose",
                            1280, load_model=False)
out["motion_first_default"] = p.tracker.max_age
print("PROBE" + json.dumps(out))
"""


@pytest.mark.skipif(importlib.util.find_spec("torch") is None,
                    reason="replay._build_processor needs torch")
def test_replay_applies_max_age_after_tracking_mode(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(_PROBE)
    proc = subprocess.run([sys.executable, str(script), str(HERE)],
                          capture_output=True, text=True, timeout=300)
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE")]
    assert line, proc.stdout[-2000:] + proc.stderr[-2000:]
    import json
    out = json.loads(line[0][5:])
    # the project's value wins over MOTION_FIRST's 60 (the app order)
    assert out["motion_first"] == [15, 9, True]
    assert out["yolo_first"] == [15, 9, True]
    # without a project value, motion_first keeps its mode default
    assert out["motion_first_default"] == config.MOTION_FIRST_BRIDGE_MAX_FRAMES
