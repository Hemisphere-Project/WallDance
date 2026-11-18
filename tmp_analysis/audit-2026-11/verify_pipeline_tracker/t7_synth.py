"""tracker#7: synthetic (zero-confidence) blob detections earn full match credit."""
from chain import *
from core.config import (TRACKER_ESTABLISHED_FRAMES, TRACK_WARMUP_THRESHOLD, MOTION_BRIDGE_WARMUP_INCREMENT,
                         TRACKER_GHOST_FROZEN_SPEED_RATIO, MOTION_FIRST_SYNTHETIC_MIN_FRAMES)

class Blob:
    def __init__(self, cx, cy, w=40.0, h=H):
        self.centroid = np.array([cx, cy], dtype=np.float64)
        self.bbox = np.array([cx - w / 2, cy - h / 2, w, h], dtype=np.float64)
        self.area = w * h

print("credit per frame: YOLO match +1.0 warm-up and 1.0 window credit; bridge +%.1f and 0.0 (config.py:879); threshold %d; established at %d hits"
      % (MOTION_BRIDGE_WARMUP_INCREMENT, TRACK_WARMUP_THRESHOLD, TRACKER_ESTABLISHED_FRAMES))
for speed_h in (0.05, 0.02):
    t = mk(); f = 0
    first_emit = None; est_at = None
    for i in range(60):
        x = 300 + i * speed_h * H
        out = t.update([], frame_number=f, motion_blobs=[Blob(x, 600)])     # a moving thing YOLO never sees
        if t.tracks and est_at is None and t.tracks[0].is_established:
            est_at = i
        if out and first_emit is None:
            first_emit = i
        f += 1
    tr = t.tracks[0]
    print("\nblob-only object moving %.2f h/frame (frozen-ghost speed gate %.2f h/frame), 60 frames, zero YOLO detections:"
          % (speed_h, TRACKER_GHOST_FROZEN_SPEED_RATIO))
    print("  track %d: hits %d, warm-up %.1f, window credits %.0f, frames_since_skeleton %d, established at frame %s, "
          "first REPORTED by the tracker at frame %s, emit reason now %s"
          % (tr.track_id, tr.hits, tr._warmup_score, sum(tr._warmup_history), tr._frames_since_skeleton, est_at,
             first_emit, t.last_emit_reasons.get(tr.track_id)))

# persistence across blob-free frames
t = mk(); f = 0
seq = [True, False, False, False, True, False, False, False, True, False]
got = []
for has in seq:
    out = t.update([], frame_number=f, motion_blobs=[Blob(300, 600)] if has else None)
    got.append("SYN" if "MOTION_SYNTHETIC_DET" in names(t, f) else ("blob" if has else "-"))
    f += 1
print("\npersistence (needs %d frames): blob present on frames 0, 4, 8 only -> %s  (cells after: %s)"
      % (MOTION_FIRST_SYNTHETIC_MIN_FRAMES, got, t._motion_blob_cells))

# what the emitted (slot) stream does with it
p, clock, rec = mk_proc()
for fr in range(80):
    clock["f"] = fr; rec.frame = fr
    trk = p.tracker
    trk.set_frame_dimensions(1280, pad_x=0); trk.set_person_height(int(H))
    tracked = trk.update([], frame_number=fr, motion_blobs=[Blob(300 + fr * 0.05 * H, 600)])
    fin = lambda t_: p._unscale_letterbox(t_, 1.0, 0, 0, roi_x=0, roi_y=0, clamp_to_yolo_size=True)
    scaled = [fin(t_) for t_ in tracked]
    outs = p._run_identity_slots(scaled, fin, 1280, 1280)
    p._emit(outs, 1280, 1280, 1, True, False, True)
counts = [m[2] for m in rec.msgs if m[1] == "/walldance/count"]
print("\nsame moving blob through the identity slots (shipped default): tracker reported it on %d of 80 frames; "
      "OSC count >= 1 on %d frames; slot events: %s"
      % (sum(1 for fr in range(80) if True) and len([1 for e in p.tracker.logger._buffer if e["event"] == "FRAME_SUMMARY" and (e["data"].get("emitted") or [])]),
         sum(1 for c in counts if c[0] >= 1), sorted({e["data"].get("event", e["data"].get("kind", "?")) for e in p.tracker.logger._buffer if e["event"] == "SLOT_EVENT"})))
