"""detection-pipeline#7 through the tuner's own replay function (tests/detect_cache.replay_from_cache_gpu)."""
import sys, json
sys.path.insert(0, "/data/WallDance/application/tests")
from common import *
import detect_cache

frames = []
for i in range(80):
    frames.append({"dets": [det(500 + 2 * i, 600)], "bc": [0.8], "gray_png": None,
                   "space": {"person_height": 120, "scale": 1.0, "pad_x": 0.0, "pad_y": 0.0,
                             "roi_x": 0, "roi_y": 0, "frame_width": 1280},
                   "ow": 1280, "oh": 1280})
cache = {"format": 1, "key": {}, "meta": {"video": "synthetic.mkv", "model": "yolo11x-pose", "imgsz": 1280,
                                          "start_frame": 0, "frames": 80, "path": "trt"}, "frames": frames}
ref = None
for ph in (120, 148, 200):
    cfg = {"person_height_px": ph, "identity_slots_enabled": True}
    s = detect_cache.replay_from_cache_gpu(cache, cfg, track_details=True, internal=True)
    pf = s["per_frame"]
    blob = json.dumps(pf, sort_keys=True, default=str)
    ref = ref or blob
    print("person_height_px=%d -> per-frame timeline (tracks + internal + emitted) identical to the 120 run: %s (%d frames, %d bytes)"
          % (ph, blob == ref, len(pf), len(blob)))
print("REBUILD_KEYS has a person-height key:", any("person_height" in k or "height" in k for k in detect_cache.REBUILD_KEYS))
import tune
print("tune.DEFAULT_SPACE person_height_px:", tune.DEFAULT_SPACE.get("person_height_px"))
