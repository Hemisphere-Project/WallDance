"""M2 (Plan Cordée rev 2): a session can be scored from its own logs -- the coordinates the stream sent are
in the event log, and the app log's lines carry a time (plan A2 ③)."""
import io
import json
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from services.app_log import _Tee

STAMP = re.compile(r"^\d\d:\d\d:\d\d\.\d{3} ")


def test_each_log_line_gets_one_time_and_the_console_none():
    console_out, console_err, f = io.StringIO(), io.StringIO(), io.StringIO()
    lock, state = threading.Lock(), {"bol": True}
    out, err = _Tee(console_out, f, lock, state), _Tee(console_err, f, lock, state)
    out.write("[Budget] fps=19.")
    out.write("7 process_wall=31.6\n")      # one line written in two pieces
    err.write("Traceback\n  line 2\n")
    out.write("")
    out.write("tail without newline")
    lines = f.getvalue().split("\n")
    assert all(STAMP.match(l) for l in lines)
    assert lines[0].endswith("[Budget] fps=19.7 process_wall=31.6") and lines[0].count(":") == 2
    assert lines[2].endswith("  line 2")
    assert console_out.getvalue() == "[Budget] fps=19.7 process_wall=31.6\ntail without newline"


def _tracks():
    from core.pipeline import ScaledTrack
    a = ScaledTrack(track_id=1, keypoints=np.zeros((17, 2)), confidence=np.zeros(17),
                    bbox=np.array([100.0, 200.0, 40.0, 100.0]), history=[], velocity=np.zeros(2),
                    smoothed_centroid=np.array([121.04, 251.96]))
    b = ScaledTrack(track_id=2, keypoints=np.zeros((17, 2)), confidence=np.zeros(17),
                    bbox=np.array([300.0, 400.0, 40.0, 100.0]), history=[], velocity=np.zeros(2))
    return [a, b]


def test_the_sent_coordinates_go_into_the_frame_summary():
    pytest.importorskip("torch")
    from core.pipeline import FrameProcessor
    got = {}
    logger = SimpleNamespace(enabled=True, annotate_frame_summary=lambda **kw: got.update(kw))
    stub = SimpleNamespace(tracker=SimpleNamespace(logger=logger), sent_xy=FrameProcessor.sent_xy)
    FrameProcessor._log_sent(stub, _tracks(), True)
    assert got == {"sent": [[1, 121.0, 252.0], [2, 320.0, 450.0]], "osc": True}
    got.clear()
    FrameProcessor._log_sent(stub, None, False)          # at L > 1 during the warm-up nothing was sent
    assert got == {}
    logger.enabled = False
    FrameProcessor._log_sent(stub, _tracks(), False)
    assert got == {}


def test_a_session_scores_from_its_event_log(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import truth_score as ts
    T = ts.Truth("synthetic", 3, (1296, 1776), 100.0, np.array([0, 1, 2]), np.array([0, 0, 0]),
                 np.array([500.0, 500.0, 500.0]), np.array([500.0, 505.0, 510.0]), np.array([0, 0, 0]),
                 np.array([[480, 450, 520, 550]] * 3, float))
    ev = tmp_path / "tracking_events.jsonl"
    rows = [{"frame": 10, "event": "FRAME_SUMMARY", "data": {"sent": [[1, 500.0, 500.0]], "osc": True}},
            {"frame": 11, "event": "SLOT_EVENT", "data": {}},
            {"frame": 11, "event": "FRAME_SUMMARY", "data": {"sent": [[1, 900.0, 1500.0]], "osc": True}},
            {"frame": 12, "event": "FRAME_SUMMARY", "data": {"n_tracks": 0}}]          # an older log line: no sent
    ev.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    got = list(ts.events_rows(ev, frame_offset=-10))
    assert [r["frame"] for r in got] == [0, 1]
    Z = ts.score_replay(T, got, 0.15)
    assert Z["emit"].tolist() == [True, False] and Z["f_n_emit_off"].tolist() == [0, 1]
