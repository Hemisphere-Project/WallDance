"""REQ-1 — import external videos into recording slots.

core/video_import.py (naming, .meta, copy vs transcode, refusals, no
leftovers) + the RecordingController orchestration (worker thread, statuses,
slot-button refresh on the main thread) + the newest-first slot history.
"""
import filecmp
import json
import os
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import core.video_import as vi
import core.video_recorder as vr
from core.input_transform import InputTransform
from runtime.recording_controller import RecordingController

NOW = datetime(2026, 10, 6, 14, 30, 5)


def _write_video(path, n=12, fps=20.0, size=(64, 48), fourcc="MJPG"):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), fps, size)
    if not w.isOpened():
        return False
    for i in range(n):
        f = np.zeros((size[1], size[0], 3), np.uint8)
        f[:, : (i * 5) % size[0] + 1] = 200           # moving edge: frames differ
        w.write(f)
    w.release()
    return os.path.getsize(path) > 0


@pytest.fixture
def rec(tmp_path):
    r = vr.VideoRecorder(projects_dir=str(tmp_path / "projects"))
    r.set_project("p")
    yield r
    r.close()


@pytest.fixture
def src_dir(tmp_path):
    d = tmp_path / "downloads"
    d.mkdir()
    return d


def _listing(rec, slot):
    return [os.path.basename(p) for _, p in rec.get_slot_info(slot).recordings]


# ---------------------------------------------------------------------------
# Copy (.avi / .mp4): naming, bytes, .meta
# ---------------------------------------------------------------------------
def test_copy_avi_naming_and_meta(rec, src_dir):
    src = src_dir / "rehearsal take.avi"
    assert _write_video(src, n=12, fps=20.0)
    res = vi.import_video(str(src), rec.recordings_dir, 4, now=NOW,
                          extra_meta={"project": "p"})
    assert os.path.basename(res.dest) == "slot_4_20261006_143005.avi"
    assert res.mode == "copy" and res.frames == 12
    assert filecmp.cmp(str(src), res.dest, shallow=False)       # lossless byte copy
    meta = json.loads(open(res.dest + ".meta").read())
    assert meta["actual_fps"] == pytest.approx(20.0)
    assert meta["frames"] == 12 and meta["meta_version"] == 2
    assert meta["slot"] == 4 and meta["file"] == "slot_4_20261006_143005.avi"
    assert meta["source"] == "import" and meta["project"] == "p"
    assert meta["size"] == [64, 48] and meta["codec"] == "MJPG"
    frm = meta["imported_from"]
    assert frm["path"] == os.path.realpath(src) and frm["mode"] == "copy"
    assert frm["size"] == os.path.getsize(src)
    assert frm["mtime"] == datetime.fromtimestamp(os.path.getmtime(src)).isoformat(
        timespec="seconds")
    assert "commit" in meta["app"]
    # listed as the slot's take, displayed with its stamp
    assert rec.get_slot_info(4).recordings[0] == ("2026-10-06 14:30:05", res.dest)
    assert sorted(os.listdir(rec.recordings_dir)) == [
        "slot_4_20261006_143005.avi", "slot_4_20261006_143005.avi.meta"]


def test_copy_mp4_keeps_extension(rec, src_dir):
    src = src_dir / "phone.MP4"
    if not _write_video(src, fourcc="mp4v"):
        pytest.skip("no mp4v writer in this OpenCV build")
    res = vi.import_video(str(src), rec.recordings_dir, 2, now=NOW)
    assert res.dest.endswith("slot_2_20261006_143005.mp4") and res.mode == "copy"
    assert _listing(rec, 2) == ["slot_2_20261006_143005.mp4"]


# ---------------------------------------------------------------------------
# Odd containers: transcoded to MJPG .avi (listable + decodable everywhere)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ext,fourcc", [(".mov", "mp4v"), (".mkv", "MJPG")])
def test_odd_container_is_transcoded(rec, src_dir, ext, fourcc):
    src = src_dir / f"clip{ext}"
    if not _write_video(src, n=15, fps=25.0, fourcc=fourcc):
        pytest.skip(f"cannot write {ext} with this OpenCV build")
    res = vi.import_video(str(src), rec.recordings_dir, 7, now=NOW)
    assert res.mode == "transcode"
    assert os.path.basename(res.dest) == "slot_7_20261006_143005.avi"
    assert res.frames == 15
    meta = json.loads(open(res.dest + ".meta").read())
    assert meta["codec"] == "MJPG" and meta["frames"] == 15
    assert meta["actual_fps"] == pytest.approx(25.0)
    assert meta["imported_from"]["mode"] == "transcode"
    assert meta["imported_from"]["transcode_quality"] == vi.IMPORT_TRANSCODE_QUALITY
    cap = cv2.VideoCapture(res.dest)
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 15
    ok, frame = cap.read()
    cap.release()
    assert ok and frame.shape == (48, 64, 3)
    assert _listing(rec, 7) == ["slot_7_20261006_143005.avi"]


def test_modes(src_dir):
    avi, mov = src_dir / "a.avi", src_dir / "b.mov"
    avi.write_bytes(b"x")
    mov.write_bytes(b"x")
    assert vi.resolve_mode(str(avi)) == "copy"
    assert vi.resolve_mode(str(mov)) == "transcode"
    assert vi.resolve_mode(str(avi), "transcode") == "transcode"
    with pytest.raises(vi.VideoImportError, match="only read .avi/.mp4"):
        vi.resolve_mode(str(mov), "copy")
    with pytest.raises(vi.VideoImportError):
        vi.resolve_mode(str(avi), "move")


def test_forced_transcode_of_an_avi(rec, src_dir):
    src = src_dir / "x.avi"
    assert _write_video(src, n=6)
    res = vi.import_video(str(src), rec.recordings_dir, 1, mode="transcode", now=NOW)
    assert res.mode == "transcode" and res.dest.endswith(".avi") and res.frames == 6


# ---------------------------------------------------------------------------
# A slot that already has takes: the import is the newest, history intact
# ---------------------------------------------------------------------------
def test_existing_slot_keeps_newest_first_history(rec, src_dir):
    rd = rec.recordings_dir
    assert _write_video(os.path.join(rd, "slot_3_20261001_100000.avi"), n=5)
    assert _write_video(os.path.join(rd, "slot_3_20261005_090000.avi"), n=5)
    assert _write_video(os.path.join(rd, "slot_30_20261009_000000.avi"), n=5)  # not slot 3
    src = src_dir / "new.avi"
    assert _write_video(src, n=9, fps=12.5)
    res = vi.import_video(str(src), rd, 3, now=NOW)
    names = _listing(rec, 3)
    assert names == ["slot_3_20261006_143005.avi", "slot_3_20261005_090000.avi",
                     "slot_3_20261001_100000.avi"]
    assert rec.get_slot_info(3).latest_path == res.dest
    # a click on the slot plays the import at its .meta fps; history index 2 still plays
    assert rec.start_playback(3)
    assert rec.playback_path == res.dest
    assert rec.status.playback_fps == pytest.approx(12.5)
    assert rec.start_playback(3, recording_index=2)
    assert rec.playback_path.endswith("slot_3_20261001_100000.avi")
    rec.stop_playback()


def test_stamp_collision_and_clock_skew(rec, src_dir):
    rd = rec.recordings_dir
    src = src_dir / "a.avi"
    assert _write_video(src, n=4)
    first = vi.import_video(str(src), rd, 5, now=NOW)
    second = vi.import_video(str(src), rd, 5, now=NOW)          # same second
    assert os.path.basename(first.dest) == "slot_5_20261006_143005.avi"
    assert os.path.basename(second.dest) == "slot_5_20261006_143006.avi"
    # a take stamped in the future (other machine's clock) stays behind the import
    assert _write_video(os.path.join(rd, "slot_6_20270101_000000.avi"), n=4)
    res = vi.import_video(str(src), rd, 6, now=NOW)
    assert os.path.basename(res.dest) == "slot_6_20270101_000001.avi"
    assert rec.get_slot_info(6).latest_path == res.dest


# ---------------------------------------------------------------------------
# Refusals leave nothing behind
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,content,match", [
    ("missing.avi", None, "file not found"),
    ("notes.txt", b"hello", "unsupported file type"),
    ("empty.mp4", b"", "file is empty"),
    ("garbage.mp4", os.urandom(4096), "cannot decode"),
])
def test_bad_sources_are_refused_cleanly(rec, src_dir, name, content, match):
    src = src_dir / name
    if content is not None:
        src.write_bytes(content)
    with pytest.raises(vi.VideoImportError, match=match):
        vi.import_video(str(src), rec.recordings_dir, 2, now=NOW)
    assert os.listdir(rec.recordings_dir) == []
    with pytest.raises(vi.VideoImportError, match="not a file"):
        vi.validate_source(str(src_dir))
    with pytest.raises(vi.VideoImportError, match="slot must be 1-9"):
        vi.import_video(str(src), rec.recordings_dir, 10)


def test_not_enough_disk_space(rec, src_dir, monkeypatch):
    src = src_dir / "a.avi"
    assert _write_video(src)
    monkeypatch.setattr(vi.shutil, "disk_usage",
                        lambda p: SimpleNamespace(total=1, used=1, free=1000))
    with pytest.raises(vi.VideoImportError, match="not enough disk space"):
        vi.import_video(str(src), rec.recordings_dir, 2, now=NOW)
    assert os.listdir(rec.recordings_dir) == []


def test_half_written_import_is_never_listed(rec):
    rd = rec.recordings_dir
    tmp = os.path.join(rd, vi.TEMP_PREFIX + "slot_2_20261006_143005.avi")
    assert _write_video(tmp)
    assert rec.get_slot_info(2).recordings == []
    assert vi.cleanup_stale_temps(rd) == 1 and os.listdir(rd) == []


def test_walldance_take_keeps_its_sidecar_facts(rec, src_dir):
    """Importing a slot file from another project: its real fps (the container
    holds the nominal one) and its recorded input transform carry over."""
    src = src_dir / "slot_1_20260901_200000.avi"
    assert _write_video(src, n=8, fps=30.0)
    (src_dir / "slot_1_20260901_200000.avi.meta").write_text(json.dumps(
        {"actual_fps": 17.4, "frames": 8,
         "input_transform": {"mirror": True, "rotation": 90, "applied_to_frames": False}}))
    res = vi.import_video(str(src), rec.recordings_dir, 8, now=NOW)
    meta = json.loads(open(res.dest + ".meta").read())
    assert meta["actual_fps"] == pytest.approx(17.4)
    assert meta["imported_from"]["meta"]["actual_fps"] == 17.4
    assert meta["input_transform"]["rotation"] == 90
    assert rec.start_playback(8)
    assert rec.playback_recorded_transform == InputTransform(True, 90)
    rec.stop_playback()


# ---------------------------------------------------------------------------
# RecordingController: worker thread + statuses + main-thread slot refresh
# ---------------------------------------------------------------------------
class _Ui:
    def __init__(self):
        self.statuses, self.recording_ui, self.toasts = [], [], []
        self.available = True

    def show_import_status(self, state, slot, source, message, progress, dest):
        self.statuses.append(SimpleNamespace(state=state, slot=slot, source=source,
                                             message=message, progress=progress,
                                             dest=dest))

    def update_recording_ui(self, **kw):
        self.recording_ui.append(kw)

    def show_toast(self, message, duration, color):
        self.toasts.append(message)

    def set_camera_dimensions(self, w, h):
        pass

    def show_slot_history_menu(self, *a):
        pass


def _controller(rec):
    ui = _Ui()
    ctrl = RecordingController(
        recorder=rec,
        tracker_logger=SimpleNamespace(flush=lambda: None),
        camera=SimpleNamespace(),
        ui=ui,
        session=SimpleNamespace(current_project="p"),
        on_playback_restart=lambda: None,
        startup_review=SimpleNamespace(pause_at_frame=None),
    )
    return ctrl, ui


def _wait(ctrl, timeout=30.0):
    t0 = time.monotonic()
    while ctrl.import_running and time.monotonic() - t0 < timeout:
        time.sleep(0.01)
    assert not ctrl.import_running


def test_controller_import_flow(rec, src_dir):
    ctrl, ui = _controller(rec)
    src = src_dir / "take.mkv"
    if not _write_video(src, n=10, fourcc="MJPG"):
        pytest.skip("cannot write .mkv")
    assert ctrl.import_video(6, str(src))
    _wait(ctrl)
    states = [s.state for s in ui.statuses]
    assert states[0] == "started" and states[-1] == "done"
    assert "transcode" in ui.statuses[0].message
    done = ui.statuses[-1]
    assert done.slot == 6 and done.progress == 1.0 and os.path.exists(done.dest)
    meta = json.loads(open(done.dest + ".meta").read())
    assert meta["project"] == "p"
    # the slot buttons refresh on the main-loop hook, not on the worker
    assert ui.recording_ui == []
    assert ctrl._drain_pending_playback_event() is None        # no tick consumed
    slots = dict(ui.recording_ui[-1]["slots_info"])
    assert slots[6] is True and len(slots) == rec.NUM_SLOTS == 9
    ui.recording_ui.clear()
    ctrl._drain_pending_playback_event()
    assert ui.recording_ui == []                                # refreshed once


def test_controller_refusals(rec, src_dir, monkeypatch):
    ctrl, ui = _controller(rec)
    assert not ctrl.import_video(2, str(src_dir / "nope.avi"))
    assert ui.statuses[-1].state == "error" and "file not found" in ui.statuses[-1].message
    assert ctrl._import_thread is None

    src = src_dir / "a.avi"
    assert _write_video(src)
    assert ctrl.import_video(2, str(src), mode="copy")             # sanity: starts
    _wait(ctrl)

    gate = threading.Event()
    real = vi.import_video
    monkeypatch.setattr(vi, "import_video", lambda *a, **k: (gate.wait(10), real(*a, **k))[1])
    assert ctrl.import_video(2, str(src))
    assert not ctrl.import_video(3, str(src))                     # one at a time
    assert "already running" in ui.statuses[-1].message
    gate.set()
    _wait(ctrl)

    rec._status.state = vr.RecorderState.RECORDING               # REC in progress
    assert not ctrl.import_video(3, str(src))
    assert "Stop the recording" in ui.statuses[-1].message
    rec._status.state = vr.RecorderState.LIVE


def test_controller_reports_worker_failure(rec, src_dir):
    ctrl, ui = _controller(rec)
    src = src_dir / "garbage.avi"
    src.write_bytes(os.urandom(2048))
    assert ctrl.import_video(1, str(src))                          # passes the quick checks
    _wait(ctrl)
    assert ui.statuses[-1].state == "error"
    assert "cannot decode" in ui.statuses[-1].message
    assert os.listdir(rec.recordings_dir) == []


def test_app_handler_is_standby_gated(rec, src_dir):
    pytest.importorskip("torch")                  # app imports the pipeline
    from app import WallDanceApp
    from runtime import api
    from runtime.api import SystemState

    ctrl, ui = _controller(rec)
    app = SimpleNamespace(system_state=SystemState.RUN, recording=ctrl)
    src = src_dir / "a.avi"
    assert _write_video(src)
    WallDanceApp._cmd_import_video(app, api.ImportVideoToSlot(2, str(src)))
    assert ui.statuses[-1].state == "error" and "STANDBY" in ui.statuses[-1].message
    assert not ctrl.import_running and rec.get_slot_info(2).recordings == []
    app.system_state = SystemState.STANDBY
    WallDanceApp._cmd_import_video(app, api.ImportVideoToSlot(2, str(src)))
    _wait(ctrl)
    assert ui.statuses[-1].state == "done"
    assert rec.get_slot_info(2).has_recordings
