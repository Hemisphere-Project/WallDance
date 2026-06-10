"""Phase 2 (2): cache-replay with INTERNAL tracker-state snapshots.

Custom replay loop (mirrors detect_cache.replay_from_cache) that records,
after every frame, ALL active tracks (not just reported): id, centroid, bbox,
hits, established, time_since_update, bridged, frames_since_skeleton -- plus
the frame's detection count.  Then per-pair analysis over concurrent
established tracks:

  overlap frames, distance distribution (h units), co-fed rate (both got a
  real skeleton the same frame, merge-frames excluded), share of frames within
  0.3/0.5/0.7 h.

For N=1 scenes every established pair is real-vs-duplicate ground truth; for
duo scenes the real pair is the highest-overlap pair (verify by eye).

Run from application/:
  .venv/Scripts/python.exe ../tmp_analysis/phase2_dupinternal.py texture-aerial
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "application"
for p in (str(APP / "tests"), str(APP / "src"), str(APP)):
    if p not in sys.path:
        sys.path.insert(0, p)

OUTROOT = HERE / "phase2" / "dupdiag"


def replay_internal(name: str):
    import cv2
    import detect_cache
    import replay
    import scoring

    man = scoring.load_scenario(str(APP / "tests" / "scenarios" / f"{name}.json"))
    config = replay.scenario_config(man)
    video = replay._find_recording(man["project"], man["slot"])
    model_name = config.get("model", "yolo11x-pose")
    imgsz = int(config.get("yolo_imgsz", 1280))
    key = detect_cache.cache_key(config, video.name, man["start"],
                                 man["frames"], model_name, imgsz)
    cpath = detect_cache.cache_path_for(key)
    if not cpath.exists():
        print(f"[{name}] building cache (GPU pass) -> {cpath.name}", flush=True)
        detect_cache.build_cache(str(video), config, model_name=model_name,
                                 imgsz=imgsz, start_frame=man["start"],
                                 max_frames=man["frames"], out_path=cpath)
    cache = detect_cache.load_cache(cpath)

    proc = replay._build_processor(config, model_name, imgsz, load_model=False)
    proc.tracker.reset()
    tmp = tempfile.mkdtemp(prefix="wd_dupint_")
    proc.tracker.logger.start_session(tmp)

    frames = []
    for i, fr in enumerate(cache["frames"]):
        gray = cv2.imdecode(np.frombuffer(fr["gray_png"], np.uint8),
                            cv2.IMREAD_GRAYSCALE)
        proc._feed_motion_detectors(gray)
        dets = [(k, c, b) for (k, c, b) in fr["dets"]]
        timing = {}
        reported = proc._track_detections(
            dets, fr["roi_x"], fr["roi_y"], fr["ow"], fr["oh"], i, timing)
        rep_ids = {int(t.track_id) for t in reported}
        snap = []
        for t in proc.tracker.tracks:
            c = t.get_centroid()
            snap.append({
                "id": int(t.track_id),
                "c": [float(c[0]), float(c[1])],
                "bbox": [float(x) for x in t.bbox],
                "hits": int(t.hits),
                "est": bool(t.is_established),
                "tsu": int(t.time_since_update),
                "bridged": bool(t.is_bridged),
                "fss": int(t._frames_since_skeleton),
                "rep": int(t.track_id) in rep_ids,
            })
        frames.append({"frame": i, "n_det": len(dets), "tracks": snap})
    proc.tracker.logger.close()

    out = OUTROOT / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "internal.json").write_text(json.dumps(frames))
    h = float(config.get("person_height_px", 200))
    analyze(name, frames, h, man)
    return frames


def analyze(name, frames, h, man):
    n_exp = man.get("expected_count")
    pair = defaultdict(lambda: {
        "overlap": 0, "d": [], "cofed": 0, "cofed_elig": 0,
        "lt03": 0, "lt05": 0, "lt07": 0, "victim_fed_alone": 0})

    for fr in frames:
        est = [t for t in fr["tracks"] if t["est"]]
        n_act = sum(1 for t in est if t["tsu"] <= 1)
        merge_frame = fr["n_det"] < n_act
        for i in range(len(est)):
            for j in range(i + 1, len(est)):
                a, b = est[i], est[j]
                k = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                p = pair[k]
                p["overlap"] += 1
                d = float(np.linalg.norm(
                    np.array(a["c"]) - np.array(b["c"]))) / h
                p["d"].append(d)
                if d < 0.3:
                    p["lt03"] += 1
                if d < 0.5:
                    p["lt05"] += 1
                if d < 0.7:
                    p["lt07"] += 1
                if not merge_frame:
                    p["cofed_elig"] += 1
                    if a["fss"] == 0 and b["fss"] == 0:
                        p["cofed"] += 1

    print(f"\n[{name}] established-pair structure (h={h:.0f}px, N={n_exp}):")
    print(f"{'pair':>9} {'ovl':>4} {'d_med':>6} {'d_p10':>6} "
          f"{'<0.3h':>6} {'<0.5h':>6} {'<0.7h':>6} {'cofed%':>7}")
    for k in sorted(pair, key=lambda k: -pair[k]["overlap"]):
        p = pair[k]
        if p["overlap"] < 10:
            continue
        d = sorted(p["d"])
        med = d[len(d) // 2]
        p10 = d[len(d) // 10]
        cf = 100 * p["cofed"] / p["cofed_elig"] if p["cofed_elig"] else 0.0
        print(f"{str(k):>9} {p['overlap']:>4} {med:>6.2f} {p10:>6.2f} "
              f"{100*p['lt03']/p['overlap']:>5.1f}% "
              f"{100*p['lt05']/p['overlap']:>5.1f}% "
              f"{100*p['lt07']/p['overlap']:>5.1f}% {cf:>6.1f}%")


if __name__ == "__main__":
    for nm in (sys.argv[1:] or ["texture-aerial"]):
        replay_internal(nm)
