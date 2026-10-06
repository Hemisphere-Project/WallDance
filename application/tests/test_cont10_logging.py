"""CONT-10 diagnostic field logs (audit 2026-10 ``01-continuity.md`` §4/§5).

* ``emission_gate`` -- the report decision as a pure function with a reason;
* ``_collect_confirmed_tracks`` records ok / warmup / frozen / slow_dup / cap;
* FRAME_SUMMARY carries the emitted id set + per-track src / wu / fss / emit;
* live runs log into per-show ``<stamp>_live`` folders with rotation and
  pruning, never into ``./tracking_events.jsonl``.

No GPU, no footage.
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE.parent))

import core.config as config  # noqa: E402
from core import tracker as T  # noqa: E402
from core.tracker import DancerTracker, emission_gate  # noqa: E402
from core.tracking_logger import (  # noqa: E402
    TrackingLogger, prune_live_sessions, session_event_files)


# --------------------------------------------------------------------------- #
# emission_gate (pure)
# --------------------------------------------------------------------------- #
def _gate(**kw):
    base = dict(hits=50, min_hits=2, frame_count=1000, integral_ok=True,
                slow_ok=False, frames_since_skeleton=0, speed=0.0,
                person_height_px=150.0, ghost_skeleton_age=3,
                requires_skeleton=True, frozen_speed_ratio=0.03)
    base.update(kw)
    return emission_gate(**base)


def test_gate_ok():
    assert _gate() == T.EMIT_OK


def test_gate_warmup_by_hits_except_in_the_first_frames():
    assert _gate(hits=1) == T.EMIT_WARMUP
    assert _gate(hits=1, frame_count=2) == T.EMIT_OK   # frame_count <= min_hits


def test_gate_warmup_by_integral_unless_the_slow_path_holds():
    assert _gate(integral_ok=False) == T.EMIT_WARMUP
    assert _gate(integral_ok=False, slow_ok=True) == T.EMIT_OK


def test_gate_frozen_needs_stale_skeleton_and_low_speed():
    assert _gate(frames_since_skeleton=4) == T.EMIT_FROZEN          # 0 < 4.5 px/f
    assert _gate(frames_since_skeleton=3) == T.EMIT_OK              # age not exceeded
    assert _gate(frames_since_skeleton=4, speed=5.0) == T.EMIT_OK   # moving
    assert _gate(frames_since_skeleton=4, ghost_skeleton_age=15) == T.EMIT_OK
    assert _gate(frames_since_skeleton=4, requires_skeleton=False) == T.EMIT_OK


def test_gate_warmup_wins_over_frozen():
    assert _gate(integral_ok=False, frames_since_skeleton=99) == T.EMIT_WARMUP


# --------------------------------------------------------------------------- #
# _collect_confirmed_tracks reasons (set-level: slow_dup, cap)
# --------------------------------------------------------------------------- #
class _Trk:
    def __init__(self, tid, *, hits=50, wu=None, slow=False, fss=0, vel=(0, 0),
                 pos=(0.0, 0.0)):
        self.track_id = tid
        self.hits = hits
        self._warmup_score = config.TRACK_WARMUP_THRESHOLD if wu is None else wu
        self.warmup_confirmed = slow or self._warmup_score >= config.TRACK_WARMUP_THRESHOLD
        self.bbox = np.array([0.0, 0.0, 60.0, 180.0])
        self._frames_since_skeleton = fss
        self._vel = np.asarray(vel, dtype=float)
        self._pos = np.asarray(pos, dtype=float)

    def get_velocity(self):
        return self._vel

    def get_last_known_position(self):
        return self._pos


def _tracker(tmp_path):
    t = DancerTracker()
    t.logger.start_session(str(tmp_path / "s"))
    t._person_height_px = 150
    t.frame_count = 10_000
    return t


def test_reasons_cover_every_track(tmp_path):
    t = _tracker(tmp_path)
    t.intermittent_confirm = True
    t.max_persons = 2
    t.tracks = [
        _Trk(1, hits=90, pos=(0, 0)),                     # ok
        _Trk(2, hits=80, pos=(900, 0)),                   # ok
        _Trk(3, hits=10, pos=(1800, 0)),                  # integral ok -> capped (3rd)
        _Trk(4, wu=3.0),                                  # warmup
        _Trk(5, fss=10),                                  # frozen
        _Trk(6, wu=5.0, slow=True, pos=(50, 0)),          # slow path, 50 px from #1
    ]
    out = [x.track_id for x in t._collect_confirmed_tracks()]
    assert out == [1, 2]
    assert t.last_emit_reasons == {1: "ok", 2: "ok", 3: "cap", 4: "warmup",
                                   5: "frozen", 6: "slow_dup"}


# --------------------------------------------------------------------------- #
# FRAME_SUMMARY fields from a real update() loop
# --------------------------------------------------------------------------- #
def _det(x, conf=0.8):
    kpts = np.tile([x, 200.0], (17, 1)).astype(float)
    return kpts, np.full(17, conf), np.array([x - 30.0, 110.0, 60.0, 180.0])


def _summaries(log_dir):
    rows = [json.loads(l) for l in open(Path(log_dir) / "tracking_events.jsonl")]
    return [r for r in rows if r.get("event") == "FRAME_SUMMARY"]


def test_frame_summary_logs_emitted_set_and_track_diagnostics(tmp_path):
    t = DancerTracker()
    t.reset()
    t.set_person_height(180)
    t.set_frame_dimensions(1280, pad_x=0)
    t.logger.start_session(str(tmp_path / "s"))
    emitted_by_frame = []
    for f in range(30):
        dets = [_det(300.0 + 2 * f)] if f != 25 else []        # one miss at f=25
        out = t.update(dets, frame_number=f)
        emitted_by_frame.append([x.track_id for x in out])
    t.logger.close()
    fs = _summaries(tmp_path / "s")
    assert len(fs) == 30
    for row, emitted in zip(fs, emitted_by_frame):
        assert row["data"]["emitted"] == emitted
        for st in row["data"]["track_states"]:
            assert {"src", "wu", "fss", "emit"} <= set(st)
    st0 = fs[0]["data"]["track_states"][0]
    assert st0["src"] == "yolo" and st0["fss"] == 0 and st0["emit"] == "warmup"
    late = fs[24]["data"]["track_states"][0]
    assert late["emit"] == "ok" and late["wu"] >= config.TRACK_WARMUP_THRESHOLD
    miss = fs[25]["data"]["track_states"][0]
    assert miss["src"] == "coast" and miss["fss"] == 1


def test_feed_source_of_synthetic_and_bridge():
    trk = T.DancerTrack(*_det(300.0))
    assert trk._feed_src == "yolo"
    trk.predict()
    assert trk._feed_src == "coast"
    k, _c, b = _det(305.0)
    trk.update(k, np.zeros(17), b)             # cold-blob synthetic det
    assert trk._feed_src == "synthetic"
    tr = DancerTracker()
    blob = SimpleNamespace(centroid=np.array([310.0, 200.0]), area=5000.0)
    trk.predict()
    tr._apply_motion_bridge(trk, blob, 5.0)
    assert trk._feed_src == "bridge"


# --------------------------------------------------------------------------- #
# Live per-show folders, rotation, pruning
# --------------------------------------------------------------------------- #
def _fs(lg, n=1):
    for _ in range(n):
        lg.log_frame_summary(n_detections=0, n_tracks=0, track_states=[],
                             n_dormant=0, matched_pairs=[], emitted=[])


def test_default_tracker_writes_nothing_in_the_working_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    t = DancerTracker()
    t.update([_det(300.0)], frame_number=0)
    t.logger.flush()
    t.logger.close()
    assert not (tmp_path / "tracking_events.jsonl").exists()
    assert config.TRACKER_EVENT_LOG_FILE is None


def test_live_session_opens_on_first_live_frame_and_carries_startup_events(tmp_path):
    root = tmp_path / "projects" / "p" / "sessions"
    lg = TrackingLogger(filepath=None)
    lg.log("SESSION_SETTINGS_LIKE", {"k": 1})            # before any session
    lg.flush()                                            # nowhere to write yet
    lg.set_live_root_provider(lambda: str(root), meta_fn=lambda: {"project": "p"})
    _fs(lg)
    lg.close()
    (live,) = list(root.iterdir())
    assert live.name.endswith("_live")
    events = [json.loads(l)["event"] for l in open(live / "tracking_events.jsonl")]
    assert events == ["SESSION_START", "SESSION_SETTINGS_LIKE", "FRAME_SUMMARY"]
    meta = json.loads((live / "session.json").read_text())
    assert meta["mode"] == "live" and meta["project"] == "p"


def test_live_session_rolls_on_project_switch_and_after_playback(tmp_path):
    state = {"root": str(tmp_path / "A" / "sessions")}
    lg = TrackingLogger(filepath=None)
    lg.set_live_root_provider(lambda: state["root"])
    _fs(lg, 3)
    first = lg.session_dir
    _fs(lg, 2)
    assert lg.session_dir == first                       # same show, same folder
    state["root"] = str(tmp_path / "B" / "sessions")      # project switch
    _fs(lg)
    second = lg.session_dir
    assert second != first and second.startswith(state["root"])
    # playback: explicit session, provider says "not live"
    state["root"] = None
    lg.start_session(str(tmp_path / "B" / "sessions" / "20260101_000000_slot3"))
    _fs(lg, 4)
    assert lg.session_dir.endswith("_slot3") and not lg.is_live_session
    state["root"] = str(tmp_path / "B" / "sessions")      # back to live
    _fs(lg)
    third = lg.session_dir
    assert third.endswith("_live") and third != second
    lg.close()
    pb = [json.loads(l)["event"] for l in
          open(tmp_path / "B" / "sessions" / "20260101_000000_slot3" / "tracking_events.jsonl")]
    assert pb.count("FRAME_SUMMARY") == 4                # flushed before the roll
    first_events = [json.loads(l)["event"] for l in open(Path(first) / "tracking_events.jsonl")]
    assert first_events.count("FRAME_SUMMARY") == 5


def test_provider_errors_never_break_logging(tmp_path):
    lg = TrackingLogger(filepath=None)

    def boom():
        raise RuntimeError("no project yet")
    lg.set_live_root_provider(boom)
    _fs(lg, 2)                                           # must not raise
    assert lg.session_dir is None


def test_rotation_segments_and_cap(tmp_path):
    lg = TrackingLogger(filepath=None, segment_bytes=600, max_segments=3,
                        flush_interval=1e9)
    lg.start_session(str(tmp_path / "s"))
    for i in range(40):
        lg.log("E", {"i": i, "pad": "x" * 40})
        if i % 5 == 4:
            lg.flush()
    lg.close()
    files = session_event_files(str(tmp_path / "s"))
    names = [os.path.basename(f) for f in files]
    assert names[-1] == "tracking_events.jsonl"
    assert len(files) == 3                               # max_segments kept
    assert all(n.startswith("tracking_events.0") for n in names[:-1])
    idx = [int(n.split(".")[1]) for n in names[:-1]]
    assert idx == sorted(idx)                            # oldest first
    # nothing lost between the kept segments, and order is preserved
    got = [json.loads(l)["data"]["i"] for f in files for l in open(f)
           if json.loads(l).get("event") == "E"]
    assert got == sorted(got) and got[-1] == 39
    head = json.loads(open(files[-1]).readline())
    assert head["event"] == "SESSION_START" and head["segment"] >= 2


def test_rotation_failure_keeps_appending_without_retrying(tmp_path, monkeypatch):
    import core.tracking_logger as TL
    calls = []

    def deny(src, dst):
        calls.append(dst)
        raise PermissionError("held open by a reader")
    monkeypatch.setattr(TL.os, "replace", deny)
    lg = TrackingLogger(filepath=None, segment_bytes=200, flush_interval=1e9)
    lg.start_session(str(tmp_path / "s"))
    for i in range(10):
        lg.log("E", {"i": i, "pad": "z" * 40})
        lg.flush()                                       # must not raise
    lg.close()
    assert len(calls) == 1                               # tried once, then off
    files = session_event_files(str(tmp_path / "s"))
    assert [os.path.basename(f) for f in files] == ["tracking_events.jsonl"]
    got = [json.loads(l)["data"]["i"] for l in open(files[0])
           if json.loads(l).get("event") == "E"]
    assert got == list(range(10))


def test_prune_live_sessions_only_touches_old_live_folders(tmp_path):
    for n in ("20260101_000000_live", "20260102_000000_live", "20260103_000000_live",
              "20260104_000000_live", "20260101_000000_slot3", "latest_notes"):
        (tmp_path / n).mkdir()
    removed = prune_live_sessions(str(tmp_path), keep=2,
                                  current=str(tmp_path / "20260101_000000_live"))
    left = sorted(p.name for p in tmp_path.iterdir())
    assert [os.path.basename(r) for r in removed] == ["20260102_000000_live"]
    assert left == ["20260101_000000_live", "20260101_000000_slot3",
                    "20260103_000000_live", "20260104_000000_live", "latest_notes"]
    assert prune_live_sessions(str(tmp_path), keep=0) == []


def test_live_sessions_pruned_on_open(tmp_path):
    root = tmp_path / "sessions"
    for d in range(5):
        (root / f"2026010{d + 1}_000000_live").mkdir(parents=True)
    lg = TrackingLogger(filepath=None)
    lg.set_live_root_provider(lambda: str(root), keep=3)
    _fs(lg)
    lg.close()
    live = sorted(p.name for p in root.iterdir())
    assert len(live) == 3 and os.path.basename(lg.session_dir) in live


def test_analyze_session_reads_rotated_segments(tmp_path):
    import analyze_session
    lg = TrackingLogger(filepath=None, segment_bytes=300, flush_interval=1e9)
    lg.start_session(str(tmp_path / "s"))
    for i in range(12):
        lg.log("E", {"i": i, "pad": "y" * 30})
        lg.flush()
    lg.close()
    assert len(session_event_files(str(tmp_path / "s"))) > 2
    got = [e["data"]["i"] for e in analyze_session.stream_parse_events(
        tmp_path / "s" / "tracking_events.jsonl") if e.get("event") == "E"]
    assert got == list(range(12))


def test_recording_controller_live_root(tmp_path):
    from runtime.recording_controller import RecordingController
    rec = SimpleNamespace(is_playing=False, on_playback_start=None)
    session = SimpleNamespace(config_dir=str(tmp_path), current_project="show1",
                              model_name="m", imgsz=960,
                              saveable_config=lambda: {"confidence": 0.5})
    lg = TrackingLogger(filepath=None)
    rc = RecordingController(rec, tracker_logger=lg, camera=None, ui=None,
                             session=session, on_playback_restart=lambda: None,
                             startup_review=SimpleNamespace(pause_at_frame=None))
    assert rc._live_sessions_root() == os.path.join(str(tmp_path), "show1", "sessions")
    _fs(lg)
    meta = json.loads((Path(lg.session_dir) / "session.json").read_text())
    assert meta["project"] == "show1" and meta["config"] == {"confidence": 0.5}
    rec.is_playing = True
    assert rc._live_sessions_root() is None
    lg.close()
    # a controller without a logger (tests, tools) still constructs
    RecordingController(rec, tracker_logger=None, camera=None, ui=None,
                        session=session, on_playback_restart=lambda: None,
                        startup_review=SimpleNamespace(pause_at_frame=None))
