"""PlaySlotRecording finds the take through another path route or by bare name.

The DEV slot sees projects/ through a junction, and remote clients send paths
from the LIVE tree; an exact string match silently found nothing.
"""
import os
from types import SimpleNamespace

from runtime.recording_controller import RecordingController


def _controller(tmp_path, paths):
    rec = SimpleNamespace(is_playing=False, on_playback_start=None,
                          get_slot_info=lambda slot: SimpleNamespace(
                              recordings=[(os.path.basename(p), p) for p in paths]))
    session = SimpleNamespace(config_dir=str(tmp_path), current_project="p",
                              model_name="m", imgsz=1280, saveable_config=lambda: {})
    rc = RecordingController(rec, tracker_logger=None, camera=None, ui=None,
                             session=session, on_playback_restart=lambda: None,
                             startup_review=SimpleNamespace(pause_at_frame=None))
    started = []
    rc._start_playback_safe = lambda slot, idx: started.append((slot, idx))
    return rc, started


def test_play_recording_matches_symlinked_path_and_bare_name(tmp_path):
    real = tmp_path / "live" / "recordings"
    real.mkdir(parents=True)
    a, b = real / "slot_2_20260403_101623.avi", real / "slot_2_20260501_090000.avi"
    a.write_bytes(b"x"); b.write_bytes(b"y")
    (tmp_path / "dev").symlink_to(tmp_path / "live")       # the DEV junction
    via_dev = [str(tmp_path / "dev" / "recordings" / p.name) for p in (a, b)]
    rc, started = _controller(tmp_path, via_dev)

    rc._play_recording(2, str(b))                           # LIVE route
    rc._play_recording(2, a.name)                           # bare name
    rc._play_recording(2, str(real / "nope.avi"))           # unknown
    assert started == [(2, 1), (2, 0)]
