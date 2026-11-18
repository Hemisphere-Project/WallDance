"""detection-pipeline#7: person_height_px swept over a detect cache does not reach the gate / tracker."""
from chain import *
import json

ph_build = int(H)                                # the height the cache was built at
# a synthetic "cache": 80 frames, a 120 px dancer + a 400 px person (inside the gate only if ph >= 160)
raw = [[det(500 + 2 * i, 600), det(900, 640, h=400.0)] for i in range(80)]

def build(ph):
    """What _run_yolo_and_track captures: detections AFTER the size gate at the build height + the space."""
    p, clock, rec = mk_proc(osc=False, person_height_px=ph)
    frames = []
    for dets in raw:
        gated = FrameProcessor._filter_duplicate_detections(p, list(dets), effective_person_height=ph)   # pipeline.py:748
        frames.append({"dets": gated, "space": space(ph)})                                              # :793-795 + detect_cache.py:171-186
    return frames

cache = build(ph_build)
print("cache built at person height %d: detections per frame %s (the 400 px box is %s)"
      % (ph_build, sorted({len(fr["dets"]) for fr in cache}), "gone" if max(len(fr["dets"]) for fr in cache) == 1 else "kept"))

def replay(frames, ph_setting):
    p, clock, rec = mk_proc(osc=False, person_height_px=ph_setting)
    rows = []
    for i, fr in enumerate(frames):
        out = step(p, clock, rec, list(fr["dets"]), i, sp=fr["space"])
        rows.append([(t.track_id, round(float(t.bbox[3]))) for t in out])
    return rows, p.tracker.get_person_height(), p.tracker.distance_threshold

ref = None
for ph in (120, 148, 200):                       # tests/tune.py DEFAULT_SPACE["person_height_px"]
    rows, trk_h, gate = replay(cache, ph)
    ref = ref or rows
    print("  replay with person_height_px=%d: tracker person height %d, match gate %d px, timeline identical to the 120 run: %s, "
          "tracks at the last frame %s" % (ph, trk_h, gate, rows == ref, rows[-1]))
live = build(200)
rows, trk_h, gate = replay(live, 200)
print("  a REAL run at person height 200 (gate up to 500 px): detections per frame %s, tracker person height %d, match gate %d px, "
      "tracks at the last frame %s" % (sorted({len(fr["dets"]) for fr in live}), trk_h, gate, rows[-1]))
