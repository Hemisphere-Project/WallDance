"""Phase 2 (2): cache-replay a scenario + analyze duplicate-track behavior.

Replays the scenario's pinned config from the detect cache (builds the cache
first if missing -- the only GPU step), writes details timeline + session log
under tmp_analysis/phase2/dupdiag/<name>/, then reports per-id:
  frames reported, bridged duty, median/min distance to nearest concurrent
  reported track (person-height units), median bbox IoU with that neighbour,
  end-of-life stats.
Also: per-frame n_detections histogram vs expected N (YOLO over-detection
pressure) from the session FRAME_SUMMARY events.

Run from application/:
  .venv/Scripts/python.exe ../tmp_analysis/phase2_dupcache.py texture-duo
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "application"
for p in (str(APP / "tests"), str(APP / "src"), str(APP)):
    if p not in sys.path:
        sys.path.insert(0, p)

OUTROOT = HERE / "phase2" / "dupdiag"


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def run(name: str):
    import detect_cache
    import replay
    import scoring

    man = scoring.load_scenario(str(APP / "tests" / "scenarios" / f"{name}.json"))
    config = replay.scenario_config(man)
    video = replay._find_recording(man["project"], man["slot"])
    if video is None:
        sys.exit(f"no recording for {name}")
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
    else:
        print(f"[{name}] cache hit: {cpath.name}", flush=True)

    out = OUTROOT / name
    out.mkdir(parents=True, exist_ok=True)
    summary = detect_cache.replay_from_cache(
        detect_cache.load_cache(cpath), config,
        log_dir=str(out), track_details=True)
    per_frame = summary.pop("per_frame")
    (out / "details.json").write_text(json.dumps(per_frame))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    sc = scoring.score_timeline(per_frame, man)
    (out / "score.json").write_text(json.dumps(sc, indent=2))
    print(f"[{name}] score={sc['score']:.3f} components={sc['components']}")

    h = float(config.get("person_height_px", 200))
    analyze(name, per_frame, out, h, man)


def analyze(name, per_frame, out, h, man):
    n_exp = man.get("expected_count")
    # --- per-id stats from the details timeline ---
    stats = defaultdict(lambda: {"frames": 0, "bridged": 0, "nn": [], "iou": [],
                                 "first": None, "last": 0})
    for r in per_frame:
        ts = r.get("tracks", [])
        for t in ts:
            s = stats[t["id"]]
            s["frames"] += 1
            s["bridged"] += 1 if t["bridged"] else 0
            if s["first"] is None:
                s["first"] = r["frame"]
            s["last"] = r["frame"]
            best_d, best_iou = None, 0.0
            for u in ts:
                if u["id"] == t["id"]:
                    continue
                d = math.dist(t["centroid"], u["centroid"]) / h
                if best_d is None or d < best_d:
                    best_d = d
                    best_iou = iou(t["bbox"], u["bbox"])
            if best_d is not None:
                s["nn"].append(best_d)
                s["iou"].append(best_iou)

    print(f"\n[{name}] per reported id (h={h:.0f}px, N={n_exp}):")
    print(f"{'id':>4} {'first':>5} {'last':>5} {'frames':>6} {'brid%':>6} "
          f"{'nn_med':>6} {'nn_p10':>6} {'<0.5h%':>6} {'iou_med':>7} {'iou_p90':>7}")
    for i in sorted(stats):
        s = stats[i]
        nn = sorted(s["nn"])
        io = sorted(s["iou"])
        if nn:
            med = nn[len(nn) // 2]
            p10 = nn[len(nn) // 10]
            sh = 100 * sum(1 for d in nn if d < 0.5) / len(nn)
            iom = io[len(io) // 2]
            io9 = io[int(len(io) * 0.9)]
            extra = f"{med:>6.2f} {p10:>6.2f} {sh:>5.1f}% {iom:>7.2f} {io9:>7.2f}"
        else:
            extra = "  (never concurrent)"
        print(f"{i:>4} {s['first']:>5} {s['last']:>5} {s['frames']:>6} "
              f"{100*s['bridged']/s['frames']:>5.1f}% {extra}")

    # --- per-frame YOLO detection pressure from the session log ---
    ev = out / "tracking_events.jsonl"
    ndet = Counter()
    nyolo = Counter()
    if ev.exists():
        for line in ev.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("event") == "FRAME_SUMMARY":
                d = e.get("data", e)
                if "n_detections" in d:
                    ndet[d["n_detections"]] += 1
                if "n_yolo_detections" in d:
                    nyolo[d["n_yolo_detections"]] += 1
    print(f"\n[{name}] n_detections/frame histogram (incl. synthetic): "
          f"{dict(sorted(ndet.items()))}")
    if nyolo:
        print(f"[{name}] n_yolo_detections/frame histogram: "
              f"{dict(sorted(nyolo.items()))}")


if __name__ == "__main__":
    for nm in (sys.argv[1:] or ["texture-duo"]):
        run(nm)
