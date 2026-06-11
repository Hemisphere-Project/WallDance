"""Headless replay harness (ROADMAP P3 Stage 0 / P4).

Replays a recorded video through the real ``FrameProcessor`` CPU path and
returns the same drop/ghost/swap/track metrics ``analyze_session.py`` reports.
This turns any motion-subsystem refactor into a measurable diff: capture a
golden summary now, re-run after the change, compare.

Usage as a script (regenerate a golden for a project's recording):

    python tests/replay.py --project residence1-solo --slot 3 \
        --start 1500 --frames 400 --out tests/golden/residence1-solo_slot3.json

Importable: ``replay_recording(...) -> dict`` for the regression test.

Notes
-----
* Forces ``use_gpu_path=False`` (the CPU ``_process_cpu`` path) and
  ``use_fp16=False`` for determinism -- numbers won't match the user's
  TensorRT/fp16 production run, but they are reproducible on *this* harness,
  which is what a regression baseline needs.
* The YOLO model itself still runs on CUDA if available; that's fine and
  deterministic enough in fp32 (the test compares with tolerances).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional


def _bootstrap_cuda_libs() -> None:
    """Make torch's bundled CUDA/cuDNN libs win over the system ones.

    The dev box has a system ``libcudnn_graph.so.9`` that shadows torch's and
    aborts on a missing symbol (``cudnnGetLibConfig``).  ``run.sh`` fixes this
    by prepending the venv's ``nvidia/*/lib`` dirs to ``LD_LIBRARY_PATH``; we
    do the same here so the harness works standalone.  ``LD_LIBRARY_PATH`` is
    read by the linker at launch, so we set it and re-exec once (guarded by a
    sentinel) before torch is ever imported.
    """
    if os.environ.get("_WD_LD_BOOTSTRAPPED"):
        return
    nvidia = sorted(Path(sys.prefix).glob("lib/python*/site-packages/nvidia/*/lib"))
    if not nvidia:
        return
    cur = os.environ.get("LD_LIBRARY_PATH", "")
    if all(str(p) in cur for p in nvidia):
        return
    want = os.pathsep.join(str(p) for p in nvidia)
    os.environ["LD_LIBRARY_PATH"] = want + (os.pathsep + cur if cur else "")
    os.environ["_WD_LD_BOOTSTRAPPED"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)


_bootstrap_cuda_libs()

import cv2  # noqa: E402

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
_APP = _HERE.parent
for p in (_SRC, _APP):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

REPO = _APP.parent
MODELS_DIR = REPO / "models"
PROJECTS_DIR = REPO / "projects"


def _latest_config(project: str) -> Optional[dict]:
    """Newest saved config for a project (the realistic, tuned settings)."""
    pdir = PROJECTS_DIR / project
    if not pdir.is_dir():
        return None
    cfgs = sorted(
        (f for f in pdir.glob("*.json") if not f.name.startswith("_")),
        key=lambda f: f.stat().st_mtime, reverse=True)
    if not cfgs:
        cfgs = sorted(pdir.glob("*.json"), key=lambda f: f.stat().st_mtime,
                      reverse=True)
    if not cfgs:
        return None
    # Flatten schema-v2 (lighting profiles) configs to the active profile's
    # flat view; v1 flat configs pass through unchanged.
    import config_schema
    return config_schema.flatten(json.loads(cfgs[0].read_text()))


def _find_recording(project: str, slot: int) -> Optional[Path]:
    recs = sorted((PROJECTS_DIR / project / "recordings").glob(
        f"slot_{slot}_*.avi")) + sorted(
        (PROJECTS_DIR / project / "recordings").glob(f"slot_{slot}_*.mp4"))
    return recs[0] if recs else None


def scenario_config(manifest: dict) -> dict:
    """Resolve a scenario's base config.

    Prefers the **pinned snapshot** in the manifest (``"config": {...}``) --
    the reproducible path: goldens regenerated from a pinned config cannot
    drift when project configs are re-saved, renamed, or deleted (the
    2026-06-10 reorganisation orphaned the original goldens exactly this way).
    Falls back to the project's latest saved config; fails loudly rather than
    silently running defaults.
    """
    pinned = manifest.get("config")
    if pinned is not None:
        return json.loads(json.dumps(pinned))  # deep copy; manifests are JSON
    cfg = _latest_config(manifest["project"])
    if cfg is None:
        raise SystemExit(
            f"scenario {manifest.get('name')!r}: no pinned config in the "
            f"manifest and no saved config for project "
            f"{manifest['project']!r} -- pin one (\"config\": {{...}}) "
            "instead of silently replaying defaults")
    return cfg


def check_fingerprint(manifest: dict, video: Path) -> None:
    """Verify the recording matches the manifest's pinned fingerprint.

    Catches re-organised / re-encoded / renumbered footage before it silently
    invalidates the scenario's ground truth (size in bytes + frame count from
    the ``.avi.meta`` sidecar when present).
    """
    fp = manifest.get("recording_fingerprint")
    if not fp:
        return
    size = video.stat().st_size
    if fp.get("bytes") is not None and fp["bytes"] != size:
        raise SystemExit(
            f"recording fingerprint mismatch for {video.name}: size {size} B "
            f"!= pinned {fp['bytes']} B -- footage changed since the ground "
            "truth was verified; re-verify GT or update the manifest")
    meta = video.with_name(video.name + ".meta")
    if fp.get("frames") is not None and meta.exists():
        try:
            frames = json.loads(meta.read_text()).get("frames")
        except (ValueError, OSError):
            frames = None
        if frames is not None and frames != fp["frames"]:
            raise SystemExit(
                f"recording fingerprint mismatch for {video.name}: "
                f"{frames} frames != pinned {fp['frames']} -- re-verify GT "
                "or update the manifest")


def _build_processor(config: dict, model_name: str, imgsz: int,
                     load_model: bool = True, use_gpu_path: bool = False):
    """Construct a FrameProcessor and apply the detection-relevant config
    subset exactly as app._apply_config_without_model does.

    ``load_model=False`` skips loading the YOLO weights (model=None) — used by
    the detect-cache replay (TUNING Phase B), which drives only the post-YOLO
    ``_track_detections`` path and never calls the model.

    ``use_gpu_path=True`` routes frames through the GPU pipeline
    (``_process_gpu`` → ``_run_yolo_and_track``) instead of ``_process_cpu`` —
    the show path. Used by the CPU↔GPU parity test (ROADMAP bug #10).
    """
    from enhancer import ImageEnhancer
    from tracker import DancerTracker
    from pipeline import FrameProcessor, ProcessingSettings
    from config import (
        YOLO_CONFIDENCE, PERSON_HEIGHT_PX, PERSON_HEIGHT_MIN_RATIO,
        PERSON_HEIGHT_MAX_RATIO, BRIGHTNESS_THRESHOLD, ENHANCE_ENABLED,
        MOTION_BRIDGE_SENSITIVITY, TrackingMode, AUTOCAL_EXCL_GRID,
        MOTION_CROSSVAL_CONFIDENT_MIN_KPTS, MOTION_CROSSVAL_CONFIDENT_MIN_CONF,
        MOTION_CROSSVAL_FRAMEDIFF_MIN_RATIO,
    )

    if load_model:
        from ultralytics import YOLO
        model_path = MODELS_DIR / f"{model_name}.pt"
        if not model_path.exists():
            raise FileNotFoundError(f"model weights not found: {model_path}")
        model = YOLO(str(model_path))
    else:
        model = None

    settings = ProcessingSettings(
        confidence=config.get("confidence", YOLO_CONFIDENCE),
        imgsz=imgsz,
        use_fp16=False,            # determinism over speed
        enhance_enabled=config.get("enhance_enabled", ENHANCE_ENABLED),
        enhance_lite=config.get("enhance_lite", False),
        enhance_force=config.get("enhance_force", False),
        person_height_px=config.get("person_height_px", PERSON_HEIGHT_PX),
        motion_sensitivity=config.get("motion_sensitivity", MOTION_BRIDGE_SENSITIVITY),
        person_height_min_ratio=config.get("person_height_min_ratio", PERSON_HEIGHT_MIN_RATIO),
        person_height_max_ratio=config.get("person_height_max_ratio", PERSON_HEIGHT_MAX_RATIO),
        # θ_s / θ_m scored-gate levers (TUNING.md's "main levers"; defaults from
        # config.py).  Surfaced as config keys so the Phase C search can set them.
        crossval_skel_min_kpts=config.get("crossval_skel_min_kpts", MOTION_CROSSVAL_CONFIDENT_MIN_KPTS),
        crossval_skel_min_conf=config.get("crossval_skel_min_conf", MOTION_CROSSVAL_CONFIDENT_MIN_CONF),
        crossval_motion_min_ratio=config.get("crossval_motion_min_ratio", MOTION_CROSSVAL_FRAMEDIFF_MIN_RATIO),
        brightness_threshold=config.get("brightness_threshold", BRIGHTNESS_THRESHOLD),
        denoise_strength=config.get("denoise_strength", 0.0),
        greyscale=config.get("greyscale", False),
        osc_enabled=False,
        use_gpu_path=use_gpu_path,  # default False: the CPU _process_cpu path
    )
    settings.roi_enabled = bool(config.get("roi_enabled", False))
    settings.roi_x = int(config.get("roi_x", 0))
    settings.roi_y = int(config.get("roi_y", 0))
    settings.roi_w = int(config.get("roi_w", 0))
    settings.roi_h = int(config.get("roi_h", 0))

    enhancer = ImageEnhancer()
    if "clahe_clip" in config:
        enhancer.clahe_clip = config["clahe_clip"]
        enhancer._update_clahe()
    if "gamma" in config:
        enhancer.gamma = config["gamma"]
        enhancer._update_gamma_lut()

    tracker = DancerTracker()
    tracker.set_person_height(settings.person_height_px)
    if "tracker_max_age" in config:
        tracker.max_age = config["tracker_max_age"]
    if "tracker_smoothing" in config:
        tracker.smoothing_depth = config["tracker_smoothing"]
    if "tracker_intermittent_confirm" in config:
        tracker.intermittent_confirm = bool(config["tracker_intermittent_confirm"])
    if "tracker_swap_correctors" in config:
        tracker.swap_correctors = bool(config["tracker_swap_correctors"])
    if "max_persons" in config:
        tracker.max_persons = int(config["max_persons"])

    proc = FrameProcessor(model=model, settings=settings,
                          enhancer=enhancer, tracker=tracker)

    # Tracking mode first (its defaults must not clobber the tuned values).
    try:
        mode = TrackingMode(config.get("tracking_mode", "yolo_first"))
    except ValueError:
        mode = TrackingMode.YOLO_FIRST
    tracker.set_tracking_mode(mode)
    proc.set_tracking_mode(mode)

    if "mog2_scale" in config and proc.motion_detector is not None:
        proc.set_motion_scale(config["mog2_scale"])
    if "mog2_var_threshold" in config:
        proc.set_motion_var_threshold(float(config["mog2_var_threshold"]))
    if "motion_sensitivity" in config:
        proc.set_motion_sensitivity(config["motion_sensitivity"])
    cells = config.get("exclusion_cells")
    manual_add = config.get("exclusion_manual_add") or ()
    manual_remove = config.get("exclusion_manual_remove") or ()
    if (cells or manual_add) and hasattr(proc, "set_exclusion"):
        grid = tuple(config.get("exclusion_grid") or AUTOCAL_EXCL_GRID)
        proc.set_exclusion(grid, cells or (), manual_add, manual_remove)

    return proc


def per_frame_record(frame_idx: int, abs_frame: int, tracks, track_details: bool = False) -> dict:
    """One timeline row from the OSC-faithful returned tracks.

    ``track_details=True`` adds spatial info (bbox/centroid/bridged) for the
    Phase-D overlay; default off so the Phase-A/B timelines stay lean and the
    cache-equivalence comparison is unaffected.
    """
    rec = {
        "frame": frame_idx,
        "abs_frame": abs_frame,
        "reported": len(tracks),
        "ids": sorted(int(t.track_id) for t in tracks),
    }
    if track_details:
        det = []
        for t in tracks:
            bbox = [float(x) for x in t.bbox]
            sc = getattr(t, "smoothed_centroid", None)
            if sc is not None:
                centroid = [float(sc[0]), float(sc[1])]
            else:
                centroid = [bbox[0] + bbox[2] / 2, bbox[1] + bbox[3] / 2]
            det.append({
                "id": int(t.track_id),
                "bbox": bbox,
                "centroid": centroid,
                "bridged": bool(getattr(t, "is_bridged", False)),
            })
        rec["tracks"] = det
    return rec


def replay_recording(
    video_path: str,
    config: dict,
    *,
    model_name: str = "yolo11x-pose",
    imgsz: int = 1280,
    start_frame: int = 0,
    max_frames: Optional[int] = None,
    log_dir: Optional[str] = None,
    track_details: bool = False,
    use_gpu_path: bool = False,
) -> Dict:
    """Replay a recording and return a compact metric summary.

    The summary mirrors analyze_session's vocabulary so goldens are
    interpretable: real/marginal/ghost track counts (by hit count),
    swap count, zero-detection frames, average detections.
    """
    proc = _build_processor(config, model_name, imgsz,
                            use_gpu_path=use_gpu_path)
    if use_gpu_path and not proc.gpu_path_active:
        raise RuntimeError("GPU path requested but unavailable "
                           "(kornia/CUDA missing?)")
    # Reset the global track-ID counter so IDs are deterministic when several
    # replays run in one process (a search, or --cache build+replay).
    proc.tracker.reset()

    tmp = log_dir or tempfile.mkdtemp(prefix="wd_replay_")
    proc.tracker.logger.start_session(tmp)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    if start_frame:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    # Per-frame reported-track timeline, captured from the OSC-faithful return of
    # process() (len(tracks) == what OSCSender.send_frame emits).  This is the
    # signal scoring.py compares against the scenario's ground-truth N.
    per_frame = []
    processed = 0
    try:
        while True:
            if max_frames is not None and processed >= max_frames:
                break
            ok, frame = cap.read()
            if not ok:
                break
            tracks, _enh, _timing, _lat = proc.process(
                frame, need_preview=False, frame_number=processed)
            per_frame.append(per_frame_record(
                processed, start_frame + processed, tracks, track_details))
            processed += 1
    finally:
        cap.release()
        proc.tracker.logger.close()

    summary = _summary_from_log(
        tmp, Path(video_path).name, model_name, imgsz, start_frame,
        processed, per_frame)
    summary["path"] = "gpu" if use_gpu_path else "cpu"
    return summary


def _summary_from_log(
    log_dir: str,
    video_name: str,
    model_name: str,
    imgsz: int,
    start_frame: int,
    processed: int,
    per_frame: list,
) -> Dict:
    """Build the (golden-comparable) metric summary from a tracker session log.

    Shared by ``replay_recording`` (live path) and the detect-cache replay
    (TUNING Phase B) so both report identical metrics.
    """
    from analyze_session import collect_stats, classify_tracks

    events_path = Path(log_dir) / "tracking_events.jsonl"
    stats = collect_stats(events_path)
    tracks = classify_tracks(stats)
    fs = stats["frame_summaries"]
    dets = [d.get("n_detections", 0) for d in fs.values()]

    return {
        "video": video_name,
        "model": model_name,
        "imgsz": imgsz,
        "start_frame": start_frame,
        "frames_processed": processed,
        "real_tracks": len(tracks["real"]),
        "marginal_tracks": len(tracks["marginal"]),
        "ghost_tracks": len(tracks["ghost"]),
        "total_tracks": len(tracks["all"]),
        "swap_count": len(stats["swap_events"]),
        "gate_rejections": len(stats["gate_events"]),
        "dormant_count": len(stats["dormant_events"]),
        "resurrect_count": len(stats["resurrect_events"]),
        "zero_detection_frames": dets.count(0),
        "avg_detections": round(sum(dets) / len(dets), 3) if dets else 0.0,
        # Per-frame reported-track timeline (for scoring.py). main() splits this
        # out of the lean golden summary written by --out.
        "per_frame": per_frame,
    }


def parse_set_value(v: str):
    """Parse a --set value: JSON first (ints/floats/bools/lists), else raw str.

    So ``--set mog2_var_threshold=20`` -> int, ``=0.02`` -> float,
    ``=true`` -> bool, ``=[16,10]`` -> list, ``=motion_first`` -> str.
    """
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return v


def apply_overrides(config: dict, sets: list) -> dict:
    """Apply ``KEY=VALUE`` overrides (from --set) onto a config dict, in place."""
    for kv in sets or []:
        key, sep, val = kv.partition("=")
        if not sep:
            raise SystemExit(f"--set expects KEY=VALUE, got: {kv!r}")
        config[key.strip()] = parse_set_value(val.strip())
    return config


def main():
    ap = argparse.ArgumentParser(description="Replay a recording -> metric summary")
    ap.add_argument("--project", default=None,
                    help="project name (or supply --scenario)")
    ap.add_argument("--slot", type=int, default=None,
                    help="recording slot (or supply --scenario)")
    ap.add_argument("--scenario", default=None,
                    help="scenario manifest JSON; fills project/slot/start/frames "
                         "and enables ground-truth scoring")
    ap.add_argument("--video", help="explicit video path (overrides --slot lookup)")
    ap.add_argument("--model", default=None, help="model name (default: project config)")
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--frames", type=int, default=None)
    ap.add_argument("--var", type=float, default=None,
                    help="override mog2_var_threshold (shorthand for --set mog2_var_threshold=)")
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE",
                    help="override any config key (repeatable); value parsed as JSON then str. "
                         "Levers: crossval_motion_min_ratio/skel_min_kpts/skel_min_conf, "
                         "mog2_var_threshold/scale, person_height_px, tracker_max_age/smoothing")
    ap.add_argument("--out", default=None, help="write lean JSON summary to this path")
    ap.add_argument("--timeline", default=None,
                    help="write the per-frame reported-count timeline to this path")
    ap.add_argument("--gpu-path", action="store_true",
                    help="route frames through the GPU pipeline (_process_gpu), "
                         "the show path -- for the CPU/GPU parity test (bug #10)")
    ap.add_argument("--details", action="store_true",
                    help="include per-track bbox/centroid in the --timeline rows")
    ap.add_argument("--score", action="store_true",
                    help="score against --scenario's ground truth (prints breakdown)")
    ap.add_argument("--cache", action="store_true",
                    help="use the YOLO detect-pass cache (TUNING Phase B): build "
                         "it on first use, then replay from it skipping YOLO")
    ap.add_argument("--rebuild-cache", action="store_true",
                    help="force-rebuild the detect-pass cache before replaying")
    args = ap.parse_args()

    scenario = None
    if args.scenario:
        import scoring
        scenario = scoring.load_scenario(args.scenario)
        args.project = args.project or scenario["project"]
        if args.slot is None:
            args.slot = scenario["slot"]
        if not args.start:
            args.start = scenario["start"]
        if args.frames is None:
            args.frames = scenario["frames"]
    if not args.project or args.slot is None:
        sys.exit("need --project and --slot (or --scenario)")

    if scenario is not None:
        config = scenario_config(scenario)
    else:
        config = _latest_config(args.project)
        if config is None:
            sys.exit(
                f"no saved config for project {args.project!r} -- refusing "
                "to silently replay defaults; use --scenario with a pinned "
                "config, or fix the project name")
    if args.var is not None:
        config["mog2_var_threshold"] = args.var
    apply_overrides(config, args.sets)
    video = args.video or _find_recording(args.project, args.slot)
    if not video:
        sys.exit(f"no recording found for {args.project} slot {args.slot}")
    if scenario is not None:
        check_fingerprint(scenario, Path(video))

    model_name = args.model or config.get("model", "yolo11x-pose")
    imgsz = args.imgsz or int(config.get("yolo_imgsz", 1280))

    if args.cache or args.rebuild_cache:
        # Detect-pass cache path (TUNING Phase B): skip YOLO, replay the tunable
        # gate/motion/tracker back-end from cached detections + motion grays.
        import detect_cache
        key = detect_cache.cache_key(
            config, Path(video).name, args.start, args.frames or 0,
            model_name, imgsz)
        cpath = detect_cache.cache_path_for(key)
        if args.rebuild_cache or not cpath.exists():
            detect_cache.build_cache(
                str(video), config, model_name=model_name, imgsz=imgsz,
                start_frame=args.start, max_frames=args.frames, out_path=cpath)
        summary = detect_cache.replay_from_cache(detect_cache.load_cache(cpath), config)
    else:
        summary = replay_recording(
            str(video), config, model_name=model_name, imgsz=imgsz,
            start_frame=args.start, max_frames=args.frames,
            track_details=args.details, use_gpu_path=args.gpu_path,
        )

    # Split the per-frame timeline out of the lean (golden-comparable) summary.
    per_frame = summary.pop("per_frame", [])
    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, indent=2))
        print(f"\nwrote {args.out}")
    if args.timeline:
        Path(args.timeline).parent.mkdir(parents=True, exist_ok=True)
        Path(args.timeline).write_text(json.dumps(per_frame))
        print(f"wrote {args.timeline}")

    if args.score:
        if scenario is None:
            sys.exit("--score requires --scenario")
        import scoring
        result = scoring.score_timeline(per_frame, scenario)
        print("\n=== SCORE ===")
        print(json.dumps(result, indent=2))
        if scenario.get("pass"):
            verdict = scoring.evaluate_pass(result, scenario)
            print("\n=== PASS LINE ===")
            print(json.dumps(verdict, indent=2))


if __name__ == "__main__":
    main()
