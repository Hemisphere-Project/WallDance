"""camera-recording#6 (actual_fps) and camera-recording#8 (playback frame labels, loop reset order).

REAL: core.video_recorder.VideoRecorder / _RecordingJob, RecordingController (event queue,
_maybe_pause_at_target_frame).  FAKE: the frame source; for #6 a slow cv2.VideoWriter.
Everything is written to a temp dir next to this script and removed.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

import cv2
import numpy as np

import core.video_recorder as vr
from core.video_recorder import VideoRecorder
from runtime.recording_controller import RecordingController

W, H = 64, 48


def quiet(fn, *a, **k):
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(*a, **k)


# ---------------------------------------------------------------------------------------------
# #6  actual_fps
# ---------------------------------------------------------------------------------------------
class SlowWriter:
    per_frame_s = 1.0 / 15.0

    def __init__(self, *a, **k): self.n = 0
    def isOpened(self): return True
    def set(self, *a): return True
    def write(self, frame):
        time.sleep(self.per_frame_s)
        self.n += 1
    def release(self): pass


def take(tmp, slot, n_frames, cam_fps, writer_fps, stall_at=None, stall_s=0.0):
    real_writer = cv2.VideoWriter
    SlowWriter.per_frame_s = 1.0 / writer_fps if writer_fps else 0.0
    cv2.VideoWriter = SlowWriter
    try:
        rec = VideoRecorder(projects_dir=tmp)
        rec.set_project("fps")
        assert quiet(rec.start_recording, slot, 20.0, (W, H), {})
        path = rec._recording_path
        frame = np.zeros((H, W, 3), np.uint8)
        t0 = time.monotonic()
        t_first = t_last = None
        nxt = t0
        for i in range(n_frames):
            if stall_at is not None and i == stall_at:
                nxt += stall_s                       # the camera sends nothing for stall_s
            while time.monotonic() < nxt:
                time.sleep(0.0005)
            rec.write_frame(frame)
            t_last = time.monotonic()
            t_first = t_first or t_last
            nxt += 1.0 / cam_fps
        quiet(rec.stop_recording, {})
        quiet(rec.wait_finalized, 60.0)
        m = json.load(open(path + ".meta"))
        span = t_last - t_first
        return m, (n_frames - 1) / span, span
    finally:
        cv2.VideoWriter = real_writer


def case_6(tmp):
    print("=== #6  .meta actual_fps vs the rate the frames were captured at ===")
    m, true_fps, span = take(tmp, 1, 100, 20.0, 0)
    print(f"  encoder keeps up, no stall      : frames {m['frames']}, capture {true_fps:.2f} fps over {span:.2f} s"
          f" -> .meta actual_fps = {m['actual_fps']}")
    m, true_fps, span = take(tmp, 2, 100, 20.0, 15.0)
    print(f"  encoder at 15 fps (backlog)     : frames {m['frames']}, capture {true_fps:.2f} fps over {span:.2f} s"
          f" -> .meta actual_fps = {m['actual_fps']}  (dropped {m['frames_dropped']})")
    m, true_fps, span = take(tmp, 3, 100, 20.0, 0, stall_at=50, stall_s=1.7)
    print(f"  encoder keeps up, one 1.7 s stall: frames {m['frames']}, {span:.2f} s wall"
          f" -> .meta actual_fps = {m['actual_fps']}  (the camera ran at 20.0 between frames)")


# ---------------------------------------------------------------------------------------------
# #8  playback labels
# ---------------------------------------------------------------------------------------------
def make_take(tmp, project, slot, n, fps_meta):
    d = os.path.join(tmp, project, "recordings")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"slot_{slot}_20260101_000000.avi")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"FFV1"), 20.0, (W, H))
    assert w.isOpened()
    for i in range(n):
        f = np.zeros((H, W, 3), np.uint8)
        f[:, :, 0] = i % 256                         # file index in the blue channel
        f[:, :, 1] = i // 256
        w.write(f)
    w.release()
    json.dump({"actual_fps": fps_meta, "frames": n}, open(path + ".meta", "w"))
    return path


def idx_of(frame):
    return int(frame[0, 0, 0]) + 256 * int(frame[0, 0, 1])


def controller(rec, tmp, pause_at=None):
    session = SimpleNamespace(config_dir=tmp, current_project="lab", model_name="m", imgsz=1280,
                              saveable_config=lambda: {}, take_provenance=lambda: {})
    return RecordingController(recorder=rec, tracker_logger=SimpleNamespace(flush=lambda: None),
                               camera=SimpleNamespace(), ui=SimpleNamespace(available=False),
                               session=session, on_playback_restart=lambda: None,
                               startup_review=SimpleNamespace(pause_at_frame=pause_at, slot=None))


def case_8a(tmp):
    print("=== #8a  --play-at-frame 100 --pause-at-frame 150 on a 300-frame take ===")
    make_take(tmp, "lab", 1, 300, 200.0)
    rec = VideoRecorder(projects_dir=tmp)
    rec.set_project("lab")
    rc = controller(rec, tmp, pause_at=150)
    assert quiet(rec.start_playback, 1, 0, start_frame=100)
    seen = []
    t_end = time.time() + 10
    while time.time() < t_end and not rec.is_paused():
        f = rec.read_frame()
        if f is None:
            time.sleep(0.0005)
            continue
        seen.append((rec.status.playback_frame, idx_of(f)))
        quiet(rc._maybe_pause_at_target_frame)       # main_loop._tick_record
    print("  first frames (label shown/logged, real file index):", seen[:5])
    print(f"  auto-pause fired at label {seen[-1][0]} = file frame {seen[-1][1]}"
          f"  (asked for frame 150; {len(seen)} frames were played)")
    quiet(rec.stop_playback)


def case_8b(tmp):
    print("=== #8b  pause, step forward 5x, resume ===")
    rec = VideoRecorder(projects_dir=tmp)
    rec.set_project("lab")
    assert quiet(rec.start_playback, 1, 0)
    seen = []
    while len(seen) < 40:
        f = rec.read_frame()
        if f is None:
            time.sleep(0.0005)
            continue
        seen.append((rec.status.playback_frame, idx_of(f)))
    quiet(rec.pause_playback)
    time.sleep(0.2)
    f = rec.read_frame()
    if f is not None:
        seen.append((rec.status.playback_frame, idx_of(f)))
    print("  at pause            (label, file index):", seen[-1])
    steps = []
    for _ in range(5):
        rec.next_frame()
        f = rec.read_frame()
        steps.append((rec.status.playback_frame, idx_of(f)))
    print("  5 steps             (label, file index):", steps)
    quiet(rec.resume_playback)
    after = []
    while len(after) < 4:
        f = rec.read_frame()
        if f is None:
            time.sleep(0.0005)
            continue
        after.append((rec.status.playback_frame, idx_of(f)))
    print("  after resume        (label, file index):", after,
          f" -> label is {after[0][1] - after[0][0]} frames behind the file")
    quiet(rec.stop_playback)


def case_8c(tmp, proc_s, label):
    make_take(tmp, "loop", 1, 30, 20.0)              # 30 frames paced at 20 fps (50 ms)
    rec = VideoRecorder(projects_dir=tmp)
    rec.set_project("loop")
    rc = controller(rec, tmp)
    assert quiet(rec.start_playback, 1, 0)
    log = []
    resets = 0
    t_end = time.time() + 12
    while time.time() < t_end and resets < 2:
        ev = quiet(rc._drain_pending_playback_event)     # main_loop._tick_pumps:565-568
        if ev is not None:
            log.append(f"RESET({ev})")
            if ev == "loop":
                resets += 1
            continue
        f = rec.read_frame()                             # main_loop._tick_acquire:639
        if f is None:
            time.sleep(0.002)
            continue
        log.append(idx_of(f))
        time.sleep(proc_s)                               # the pipeline processes the frame
    quiet(rec.stop_playback)
    i = log.index("RESET(loop)")
    print(f"  {label}: ... {log[max(0, i - 3):i + 4]}")
    stale = [x for x in log[i + 1:i + 3] if isinstance(x, int) and x >= 25]
    print(f"      frames of the PREVIOUS pass processed after the tracker reset: {stale}")


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="wd_rec_", dir=os.path.dirname(os.path.abspath(__file__)))
    try:
        which = sys.argv[1:] or ["6", "8"]
        if "6" in which:
            case_6(tmp)
        if "8" in which:
            case_8a(tmp)
            case_8b(tmp)
            print("=== #8c  loop: tracker reset vs the last frame of the pass (30 frames @ 20 fps) ===")
            case_8c(tmp, 0.020, "pipeline 20 ms/frame (faster than the take)")
            case_8c(tmp, 0.080, "pipeline 80 ms/frame (slower than the take)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
