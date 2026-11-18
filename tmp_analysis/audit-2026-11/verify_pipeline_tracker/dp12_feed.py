"""detection-pipeline#12: the motion feed stays in flight when YOLO raises in _run_yolo_and_track."""
import time, threading
from chain import *

p, clock, rec = mk_proc(osc=False)
log = []
orig_feed = p._feed_motion_detectors

def slow_feed(gray):
    log.append(("feed start", threading.current_thread().name))
    time.sleep(0.3)                       # a slow MOG2 feed (the dark-scene case)
    orig_feed(gray)
    log.append(("feed end", threading.current_thread().name))

p._feed_motion_detectors = slow_feed

def boom(_tensor):
    raise AssertionError("input size torch.Size([1, 3, 1280, 1280]) not equal to max model size (1, 3, 960, 960)")

p._run_yolo = boom
gray = np.zeros((720, 720), np.uint8)
p._submit_motion_feed(gray)               # what _start_motion_feed does
motion = (time.perf_counter(), gray)
try:
    p._run_yolo_and_track(None, {"roi": {}, "letterbox": {}}, {}, 1280, 1280, frame_number=0, raw_frame=gray, motion=motion)
except AssertionError as e:
    fut = p._motion_future
    print("YOLO raised: %s..." % str(e)[:40])
    print("  after the exception: _motion_future is %s, done=%s, worker log so far=%s"
          % ("still set" if fut is not None else "None", fut.done() if fut is not None else None, log))
    # main-thread code that the frame-error path can reach while the worker is mid-feed:
    t0 = time.perf_counter()
    p.motion_model.reset()                # e.g. FrameProcessor.reset path / set_motion_scale -> MotionDetector.reset()
    print("  motion_model.reset() ran on %s after %.0f ms while the feed was %s"
          % (threading.current_thread().name, (time.perf_counter() - t0) * 1000,
             "still running" if not fut.done() else "finished"))
    fut.result()
    print("  feed finished afterwards on the reset model: detector frame_count=%d (a feed applied after the reset)"
          % p.motion_model.detector.frame_count)
print("guarded stage for comparison (pipeline.py:614-619, 671-677): GPU-stage exceptions call _await_motion_feed()")
print("executor shutdown calls in src:", "none (grep _motion_executor: only pipeline.py:458, 1757)")
