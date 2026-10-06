"""services/crash_marker.py: the next start learns how the last session ended."""
import json

from services import crash_marker as cm


def test_first_start_reports_nothing_and_marks_running(tmp_path):
    assert cm.begin_session(tmp_path, version="v1") is None
    sentinel = json.loads((tmp_path / cm.SENTINEL).read_text())
    assert sentinel["version"] == "v1" and sentinel["project"] is None
    cm.end_session(tmp_path)
    assert not (tmp_path / cm.SENTINEL).exists()
    assert cm.begin_session(tmp_path) is None          # clean exit -> nothing


def test_exception_marker_is_reported_once_and_archived(tmp_path):
    cm.begin_session(tmp_path)
    path = cm.write_crash(tmp_path, reason="persistent frame processing failure",
                          exception="RuntimeError: CUDA error",
                          traceback_text="Traceback...\nRuntimeError: CUDA error\n",
                          project="hangar", state="RUN", log_path=tmp_path / "x.log")
    assert path == tmp_path / cm.MARKER
    cm.end_session(tmp_path)

    info = cm.begin_session(tmp_path)
    assert info["kind"] == "exception"
    assert info["project"] == "hangar" and info["state"] == "RUN"
    assert "CUDA error" in info["traceback"]
    assert not (tmp_path / cm.MARKER).exists()
    assert json.loads((tmp_path / cm.REPORTED).read_text())["reason"] == \
        "persistent frame processing failure"
    cm.end_session(tmp_path)
    assert cm.begin_session(tmp_path) is None          # reported only once


def test_leftover_sentinel_means_unclean_exit(tmp_path):
    cm.begin_session(tmp_path, log_path=tmp_path / "walldance_old.log")
    cm.note_project(tmp_path, "residence1-solo")
    # ... native crash / kill / power loss: no end_session, no marker ...
    info = cm.begin_session(tmp_path)
    assert info["kind"] == "unclean_exit"
    assert info["project"] == "residence1-solo"
    assert info["log"].endswith("walldance_old.log")
    assert "faulthandler" in info["reason"]


def test_corrupt_marker_still_reported(tmp_path):
    (tmp_path / cm.MARKER).write_text("{trunc", encoding="utf-8")
    info = cm.begin_session(tmp_path)
    assert info["kind"] == "exception" and "unreadable" in info["reason"]


def test_format_report(tmp_path):
    info = {"kind": "exception", "time": "2026-10-06T21:30:15",
            "reason": "uncaught exception in the main loop",
            "exception": "RuntimeError: CUDA error: an illegal memory access",
            "traceback": "Traceback (most recent call last):\n  File x\nRuntimeError: boom\n",
            "project": "hangar", "state": "RUN", "log": "logs/walldance_1.log"}
    console, toast = cm.format_report(info)
    assert "[Crash]" in console and "RuntimeError: boom" in console
    assert "logs/walldance_1.log" in console
    assert toast.startswith("Previous session crashed (21:30, hangar)")
    assert "illegal memory access" in toast


def test_never_raises_on_an_unwritable_dir(tmp_path):
    blocker = tmp_path / "file_not_dir"
    blocker.write_text("x")
    assert cm.write_crash(blocker / "logs", reason="r") is None
    assert cm.begin_session(blocker / "logs") is None
    cm.note_project(blocker / "logs", "p")
    cm.end_session(blocker / "logs")
