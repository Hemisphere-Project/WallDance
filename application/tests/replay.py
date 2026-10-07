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
* Track P (2026-06): the CPU path was removed — replays run on the GPU pipeline.
  Pass ``--trt`` for the production show path (TensorRT FP16, byte-stable
  run-to-run on a fixed engine); without it the GPU + PyTorch-FP32 backend runs.
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


def engine_dir() -> Path:
    """Where ``--trt`` looks for ``<model>_<imgsz>.engine``.

    TensorRT engines are tied to the TRT version + GPU that built them (dev37's
    TRT 11.3 refuses the laptop's: serialization tag 243 vs 244), so a box can
    keep its own set apart from the laptop-copied ``models/``:
    ``WD_ENGINE_DIR=models/dev37`` (or ``--engine-dir``), relative to the repo.
    The env var also reaches the tools that run replay.py as a subprocess.
    """
    d = os.environ.get("WD_ENGINE_DIR")
    if not d:
        return MODELS_DIR
    p = Path(d)
    return p if p.is_absolute() else REPO / p


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
    import core.config_schema as config_schema

    return config_schema.flatten(json.loads(cfgs[0].read_text()))


def _find_recording(project: str, slot: int) -> Optional[Path]:
    # NEWEST recording for the slot — timestamped filenames
    # (slot_<n>_<YYYYMMDD_HHMMSS>.ext) sort chronologically, so [-1] is the
    # latest take. This matches the recorder's newest-first ordering and the
    # app's slot-by-latest_path selection (so the in-app sweep/dry-run scores the
    # take the operator just recorded, not an older one). Single-take slots — the
    # corpus goldens — are unaffected ([-1] == [0]), so pinned fingerprints hold.
    recs = sorted(list((PROJECTS_DIR / project / "recordings").glob(f"slot_{slot}_*.avi"))
                  + list((PROJECTS_DIR / project / "recordings").glob(f"slot_{slot}_*.mp4")))
    return recs[-1] if recs else None


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


def input_transform_for(config: Optional[dict]):
    """REQ-5: the Mirror/Rotate a replay must apply to decoded slot frames.

    Slot recordings store RAW sensor frames; the app applies the project's
    current ``InputTransform`` at playback, so ROI / exclusion mask /
    calibration live in the transformed space.  Offline tools that decode
    slot files themselves must do the same after every frame read:
    ``frame = input_transform_for(config).apply(frame)``.  Absent / invalid
    keys mean identity, whose ``apply`` returns the same array (no copy).
    """
    from core.input_transform import InputTransform
    return InputTransform.from_config(config)


def _build_processor(config: dict, model_name: str, imgsz: int,
                     load_model: bool = True, use_gpu_path: bool = False,
                     use_trt: bool = False):
    """Construct a FrameProcessor and apply the detection-relevant config
    subset exactly as app._apply_config_without_model does.

    ``load_model=False`` skips loading the YOLO weights (model=None) — used by
    the detect-cache replay (TUNING Phase B), which drives only the post-YOLO
    ``_track_detections`` path and never calls the model.

    ``use_gpu_path=True`` routes frames through the GPU pipeline
    (``_process_gpu`` → ``_run_yolo_and_track``) instead of ``_process_cpu`` —
    the show path. Used by the CPU↔GPU parity test (ROADMAP bug #10).
    """
    from core.enhancer import ImageEnhancer
    from core.tracker import DancerTracker
    from core.pipeline import FrameProcessor, ProcessingSettings
    from core.config import (
        YOLO_CONFIDENCE, PERSON_HEIGHT_PX, PERSON_HEIGHT_MIN_RATIO,
        PERSON_HEIGHT_MAX_RATIO, BRIGHTNESS_THRESHOLD, ENHANCE_ENABLED,
        MOTION_BRIDGE_SENSITIVITY, TrackingMode, AUTOCAL_EXCL_GRID,
        MOTION_CROSSVAL_CONFIDENT_MIN_KPTS, MOTION_CROSSVAL_CONFIDENT_MIN_CONF,
        MOTION_CROSSVAL_FRAMEDIFF_MIN_RATIO,
    )

    if load_model:
        from ultralytics import YOLO
        if use_trt:
            # Production-faithful: the FP16 TensorRT engine on the GPU show path.
            engine_path = engine_dir() / f"{model_name}_{imgsz}.engine"
            if not engine_path.exists():
                raise FileNotFoundError(
                    f"TRT engine not found: {engine_path} "
                    f"(build via extra/build_engines.sh)")
            model = YOLO(str(engine_path), task="pose")
        else:
            model_path = MODELS_DIR / f"{model_name}.pt"
            if not model_path.exists():
                raise FileNotFoundError(f"model weights not found: {model_path}")
            model = YOLO(str(model_path))
    else:
        model = None

    settings = ProcessingSettings(
        confidence=config.get("confidence", YOLO_CONFIDENCE),
        imgsz=imgsz,
        use_fp16=use_trt,          # TRT engine is FP16; .pt/CPU path stays FP32 (determinism)
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
        # Output box-clamp (Track X) defaults ON (matches the shipped default);
        # overridable via --set box_clamp_enabled=false for the A/B bbox check.
        box_clamp_enabled=bool(config.get("box_clamp_enabled", True)),
        use_gpu_path=use_gpu_path or use_trt,  # TRT implies the GPU show path
    )
    # Output stage (identity slots, CONT-6) as app._apply_config_without_model:
    # output-only, so the tracker summary / lean timeline do not depend on it.
    from core.config import (IDENTITY_SLOTS_ENABLED, IDENTITY_SLOTS_MAX_DANCERS,
                             IDENTITY_SLOTS_STABILITY, IDENTITY_SLOTS_COAST_S,
                             IDENTITY_SLOTS_USE_IR_BELT, IDENTITY_SLOTS_STATIC_GUARD,
                             IDENTITY_SLOTS_STATIC_RELEASE_S, IDENTITY_SLOTS_FILTER_INPUT,
                             IDENTITY_SLOTS_SMART_HOLD, FOREGROUND_ENABLED)
    settings.identity_slots_enabled = bool(config.get("identity_slots_enabled",
                                                      IDENTITY_SLOTS_ENABLED))
    settings.max_dancers = int(config.get("max_dancers", IDENTITY_SLOTS_MAX_DANCERS))
    settings.stability = float(config.get("stability", IDENTITY_SLOTS_STABILITY))
    settings.coast_s = float(config.get("coast_s", IDENTITY_SLOTS_COAST_S))
    settings.use_ir_belt = bool(config.get("use_ir_belt", IDENTITY_SLOTS_USE_IR_BELT))
    settings.static_ghost_guard = bool(config.get("static_ghost_guard", IDENTITY_SLOTS_STATIC_GUARD))
    settings.static_release_s = float(config.get("static_release_s", IDENTITY_SLOTS_STATIC_RELEASE_S))
    settings.slot_filter_input = str(config.get("slot_filter_input", IDENTITY_SLOTS_FILTER_INPUT))
    settings.smart_hold = bool(config.get("smart_hold", IDENTITY_SLOTS_SMART_HOLD))
    # clean-plate foreground: "fg_plate" (project-relative .npz, resolved by main() into
    # "fg_plate_path"); absent = no plate = the base behaviour (goldens unchanged)
    settings.fg_enabled = bool(config.get("fg_enabled", FOREGROUND_ENABLED))
    settings.belt_backing = bool(config.get("belt_backing", True))
    settings.fg_plate_path = str(config.get("fg_plate_path", "") or "")
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
    if "tracker_smoothing" in config:
        tracker.smoothing_depth = config["tracker_smoothing"]
    if "tracker_intermittent_confirm" in config:
        tracker.intermittent_confirm = bool(config["tracker_intermittent_confirm"])
    if "tracker_ghost_skeleton_age" in config:
        tracker.ghost_skeleton_age = int(config["tracker_ghost_skeleton_age"])
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
    # tracker_max_age AFTER the mode, as app._apply_config_without_model does
    # (CONT-1 / BUG-3): set_tracking_mode(MOTION_FIRST) resets max_age to
    # MOTION_FIRST_BRIDGE_MAX_FRAMES (60), which silently overrode the project
    # value in replays of motion_first projects.  yolo_first is unaffected
    # (the mode switch is a no-op from the default mode).
    if "tracker_max_age" in config:
        tracker.max_age = config["tracker_max_age"]

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


def _attach_reference_capture(proc) -> dict:
    """Hook the GPU detect-pass capture point to record per-frame reference
    detections (``holder["ref"]``, reset by the caller before each frame) --
    the continuity pseudo ground truth (``continuity.reference_from_dets``).
    Observation only: the hook runs after YOLO + duplicate filtering, before
    the post-YOLO chain, and changes nothing.  ``holder["space"]`` keeps the
    frame's tracker space (for ``internal_tracks``)."""
    import continuity
    holder: dict = {"ref": None, "space": None}
    prev = proc._cache_capture_gpu

    def _hook(dets, space, gray, ow, oh):
        confs = [proc._last_box_confs.get(proc._bbox_conf_key(b)) for (_k, _c, b) in dets]
        holder["ref"] = continuity.reference_from_dets(dets, space, confs)
        holder["space"] = space
        if prev is not None:
            prev(dets, space, gray, ow, oh)

    proc._cache_capture_gpu = _hook
    return holder


def _attach_frame_clock(proc, fps: Optional[float]) -> dict:
    """Drive the output stage (identity slots, One-Euro) on a frame clock:
    ``t = frame / fps``.  A replay runs slower or faster than real time; the
    live app uses the wall clock.  The caller bumps ``clock["frame"]``."""
    clock = {"frame": 0}
    rate = float(fps or 20.0)
    setter = getattr(proc, "set_output_clock", None)
    if callable(setter):
        setter(lambda: clock["frame"] / rate)
    return clock


def internal_tracks(tracker, space) -> list:
    """Every internal (active) tracker track in original-frame px, with its
    emit/hide reason -- the slot layer's offline input beyond the reported set
    (diagnostics; observation only)."""
    if space is None:
        return []
    g = space   # _TrackerSpace, or the detect-cache dict of it
    get = (lambda k: g[k]) if isinstance(g, dict) else (lambda k: getattr(g, k))
    s = float(get("scale")) or 1.0
    px, py = float(get("pad_x")), float(get("pad_y"))
    rx, ry = float(get("roi_x")), float(get("roi_y"))
    reasons = getattr(tracker, "last_emit_reasons", {}) or {}
    out = []
    for t in tracker.tracks:
        c = t.get_centroid()
        sm = t.get_smoothed_centroid()
        out.append({
            "id": int(t.track_id),
            "p": [round((float(c[0]) - px) / s + rx, 2), round((float(c[1]) - py) / s + ry, 2)],
            "sm": [round((float(sm[0]) - px) / s + rx, 2), round((float(sm[1]) - py) / s + ry, 2)],
            "h": round(float(t.bbox[3]) / s, 1),
            "tsu": int(t.time_since_update),
            "hits": int(t.hits),
            "fss": int(t._frames_since_skeleton),
            "emit": reasons.get(t.track_id),
        })
    return out


def _track_dict(t) -> dict:
    """Timeline entry for one reported / emitted track (``--details``)."""
    bbox = [float(x) for x in t.bbox]
    sc = getattr(t, "smoothed_centroid", None)
    if sc is not None:
        centroid = [float(sc[0]), float(sc[1])]
    else:
        centroid = [bbox[0] + bbox[2] / 2, bbox[1] + bbox[3] / 2]
    d = {
        "id": int(t.track_id),
        "bbox": bbox,
        "centroid": centroid,
        "bridged": bool(getattr(t, "is_bridged", False)),
    }
    # Additive slot-layer inputs (CONT-6): raw KF centroid + evidence.
    raw = getattr(t, "centroid_raw", None)
    if raw is not None:
        d["raw"] = [float(raw[0]), float(raw[1])]
    for key, attr in (("fss", "frames_since_skeleton"), ("hits", "hits"),
                      ("age", "age"), ("tsu", "time_since_update")):
        v = getattr(t, attr, None)
        if v is not None:
            d[key] = int(v)
    for key, attr in (("src", "feed_src"), ("state", "slot_state")):
        v = getattr(t, attr, None)
        if v is not None:
            d[key] = v
    return d


def per_frame_record(frame_idx: int, abs_frame: int, tracks, track_details: bool = False,
                     emitted=None) -> dict:
    """One timeline row from the OSC-faithful returned tracks.

    ``track_details=True`` adds spatial info (bbox/centroid/bridged) for the
    Phase-D overlay; default off so the Phase-A/B timelines stay lean and the
    cache-equivalence comparison is unaffected.  ``emitted`` (the identity-slot
    stream actually sent to OSC, ``FrameProcessor.last_emitted``) is recorded
    under ``"emitted"`` with the same row shape, so it can be scored on its own.
    """
    rec = {
        "frame": frame_idx,
        "abs_frame": abs_frame,
        "reported": len(tracks),
        "ids": sorted(int(t.track_id) for t in tracks),
    }
    if track_details:
        rec["tracks"] = [_track_dict(t) for t in tracks]
    if emitted is not None and track_details:
        # Only with details: the lean timeline stays byte-identical to the
        # decomp-phase0 goldens (replay_sweep byte-compares it).
        rec["emitted"] = {"reported": len(emitted),
                          "ids": sorted(int(t.track_id) for t in emitted),
                          "tracks": [_track_dict(t) for t in emitted]}
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
    use_gpu_path: bool = True,    # Track P: GPU-only (CPU path removed)
    use_trt: bool = False,
    frame_skip: int = 1,
    reference: bool = False,
    internal: bool = False,
    fps: Optional[float] = None,
) -> Dict:
    """Replay a recording and return a compact metric summary.

    The summary mirrors analyze_session's vocabulary so goldens are
    interpretable: real/marginal/ghost track counts (by hit count),
    swap count, zero-detection frames, average detections.

    ``reference=True`` adds each frame's YOLO detections as ``ref`` (the
    continuity pseudo ground truth, see ``reference_dets``) to the timeline rows.
    ``internal=True`` adds every internal tracker track as ``int`` (offline
    slot-layer studies).  The output stage (identity slots) runs on a frame
    clock at ``fps`` (default 20) so its seconds-based timers are replay-exact;
    the emitted stream is recorded under ``emitted`` in each row.
    """
    proc = _build_processor(config, model_name, imgsz,
                            use_gpu_path=use_gpu_path, use_trt=use_trt)
    # REQ-5: slot files hold RAW frames; apply the project's Mirror/Rotate like
    # the app's playback does (identity = the same array, byte-identical).
    xf = input_transform_for(config)
    if (use_gpu_path or use_trt) and not proc.gpu_path_active:
        raise RuntimeError("GPU path requested but unavailable "
                           "(kornia/CUDA missing?)")
    # Reset the global track-ID counter so IDs are deterministic when several
    # replays run in one process (a search, or --cache build+replay).
    proc.tracker.reset()
    ref_holder = (_attach_reference_capture(proc)
                  if (reference or internal) else None)
    clock = _attach_frame_clock(proc, fps)

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
    processed = 0      # frames actually fed to the pipeline
    consumed = 0       # source frames advanced past (read + grabbed)
    stride = max(1, int(frame_skip))
    try:
        while True:
            if max_frames is not None and consumed >= max_frames:
                break
            ok, frame = cap.read()
            if not ok:
                break
            frame = xf.apply(frame)
            if ref_holder is not None:
                ref_holder["ref"] = None
            clock["frame"] = consumed
            tracks, _enh, _timing, _lat = proc.process(
                frame, need_preview=False, frame_number=processed)
            per_frame.append(per_frame_record(
                processed, start_frame + consumed, tracks, track_details,
                emitted=getattr(proc, "last_emitted", None)))
            if reference and ref_holder["ref"] is not None:
                per_frame[-1]["ref"] = ref_holder["ref"]
            if internal:
                per_frame[-1]["int"] = internal_tracks(proc.tracker, ref_holder["space"])
                fg = getattr(proc, "last_fg", None)
                if fg is not None:     # clean-plate foreground summary (offline studies)
                    per_frame[-1]["fg"] = {
                        "v": bool(fg.valid), "why": fg.reason, "r": round(float(fg.fg_ratio), 4),
                        "g": round(float(fg.gain), 3),
                        "b": [[round(b.x), round(b.y), round(b.area)] for b in fg.blobs[:6]]}
            processed += 1
            consumed += 1
            # Frame-skip (stride): advance past the next stride-1 source frames
            # without decoding -- cheap exploration / Track-G frame-skip safety
            # pre-check.  stride==1 leaves this a no-op (consumed == processed),
            # so a default run is byte-identical to before.
            if stride > 1:
                for _ in range(stride - 1):
                    if max_frames is not None and consumed >= max_frames:
                        break
                    if not cap.grab():
                        break
                    consumed += 1
    finally:
        cap.release()
        proc.tracker.logger.close()

    summary = _summary_from_log(
        tmp, Path(video_path).name, model_name, imgsz, start_frame,
        processed, per_frame)
    summary["path"] = "trt" if use_trt else ("gpu" if use_gpu_path else "cpu")
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


def _stream_fps(scenario: Optional[dict], video: Path) -> float:
    """The recording's frame rate: the manifest's ``fps``, else the
    ``.avi.meta`` sidecar's ``actual_fps``, else 20 (the show cadence)."""
    if scenario is not None and scenario.get("fps"):
        return float(scenario["fps"])
    meta = video.with_name(video.name + ".meta")
    try:
        fps = json.loads(meta.read_text()).get("actual_fps")
        if fps:
            return float(fps)
    except (OSError, ValueError, AttributeError):
        pass
    return 20.0


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
    ap.add_argument("--trt", action="store_true",
                    help="load the <model>_<imgsz>.engine FP16 TensorRT engine and "
                         "run the GPU show path (production-faithful; implies --gpu-path)")
    ap.add_argument("--engine-dir", default=None, metavar="DIR",
                    help="--trt engine directory (default models/, or $WD_ENGINE_DIR); "
                         "e.g. models/dev37 for this box's own TRT build")
    ap.add_argument("--details", action="store_true",
                    help="include per-track bbox/centroid in the --timeline rows")
    ap.add_argument("--log-dir", default=None,
                    help="keep the tracker JSONL event log in this dir (diagnostics)")
    ap.add_argument("--score", action="store_true",
                    help="score against --scenario's ground truth (prints breakdown + the "
                         "continuity block C1-C10 and the per-window pass rate; implies "
                         "--details and records reference detections in the timeline)")
    ap.add_argument("--cache", action="store_true",
                    help="use the YOLO detect-pass cache (TUNING Phase B): build "
                         "it on first use, then replay from it skipping YOLO")
    ap.add_argument("--rebuild-cache", action="store_true",
                    help="force-rebuild the detect-pass cache before replaying")
    ap.add_argument("--frame-skip", type=int, default=1, metavar="N",
                    help="process every Nth frame (stride; default 1 = all frames). "
                         "Cheap exploration / Track-G frame-skip safety pre-check; "
                         "N=1 is byte-identical to a full run. With --cache the cache "
                         "is still built full and the stride is applied at replay.")
    ap.add_argument("--internal", action="store_true",
                    help="also record every internal tracker track per frame (row key "
                         "'int') -- offline identity-slot studies")
    ap.add_argument("--quality", action="store_true",
                    help="output-quality block without ground truth (field takes): "
                         "records reference detections; prints ids, coasting share, "
                         "jitter at rest and lag on fast moves for the tracker and the "
                         "emitted (identity-slot) streams")
    args = ap.parse_args()
    if args.engine_dir:
        os.environ["WD_ENGINE_DIR"] = args.engine_dir   # subprocess tools inherit it

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
        # Identity slots (CONT-6): the operator sets max_dancers to the show's
        # dancer cap; a scenario's cap is its largest expected count.
        config.setdefault("max_dancers", max(1, scoring.max_expected(scenario)))
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
    if config.get("fg_plate"):
        plate = Path(str(config["fg_plate"]))
        config["fg_plate_path"] = str(plate if plate.is_absolute()
                                      else PROJECTS_DIR / args.project / plate)
    video = args.video
    if video is None and scenario is not None and scenario.get("video"):
        # A manifest may pin a file other than the slot's recording (e.g. a
        # clip cut from it), relative to the project folder.
        video = PROJECTS_DIR / scenario["project"] / scenario["video"]
    video = video or _find_recording(args.project, args.slot)
    if not video:
        sys.exit(f"no recording found for {args.project} slot {args.slot}")
    if scenario is not None:
        check_fingerprint(scenario, Path(video))

    model_name = args.model or config.get("model", "yolo11x-pose")
    imgsz = args.imgsz or int(config.get("yolo_imgsz", 1280))
    fps = _stream_fps(scenario, Path(video))
    want_ref = args.score or args.quality

    if args.cache or args.rebuild_cache:
        # Detect-pass cache path (TUNING Phase B): skip YOLO, replay the tunable
        # gate/motion/tracker back-end from cached detections + motion grays.
        import detect_cache
        path_tag = "trt" if args.trt else "gpu"   # Track P: GPU show-path cache
        key = detect_cache.cache_key(
            config, Path(video).name, args.start, args.frames or 0,
            model_name, imgsz, path=path_tag)
        cpath = detect_cache.cache_path_for(key)
        if args.rebuild_cache or not cpath.exists():
            # Cache is built full (stride-independent, reusable); the stride is
            # applied at replay time below.
            detect_cache.build_cache_gpu(
                str(video), config, model_name=model_name, imgsz=imgsz,
                start_frame=args.start, max_frames=args.frames, out_path=cpath,
                use_trt=args.trt)
        summary = detect_cache.replay_from_cache_gpu(
            detect_cache.load_cache(cpath), config, frame_skip=args.frame_skip,
            track_details=args.details or want_ref, reference=want_ref,
            internal=args.internal, fps=fps)
    else:
        summary = replay_recording(
            str(video), config, model_name=model_name, imgsz=imgsz,
            start_frame=args.start, max_frames=args.frames,
            # --score: centroids (C7/C10) + reference dets (C7/C8/C9) for the
            # continuity block; the lean --out summary is unaffected.
            track_details=args.details or want_ref, use_gpu_path=True,   # Track P: GPU-only
            use_trt=args.trt, log_dir=args.log_dir, frame_skip=args.frame_skip,
            reference=want_ref, internal=args.internal, fps=fps,
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
        import continuity
        result = scoring.score_timeline(per_frame, scenario)
        print("\n=== SCORE ===")
        print(json.dumps(result, indent=2))
        # TEST-1: continuity C1-C10 + per-window pass rate (audit 01-continuity §3.3).
        cont = scoring.score_continuity(per_frame, scenario)
        print("\n=== CONTINUITY (C1-C10) ===")
        print(continuity.format_report(cont))
        print(json.dumps(cont["metrics"], indent=2))
        if scenario.get("pass"):
            verdict = scoring.evaluate_pass(result, scenario, continuity=cont["metrics"])
            print("\n=== PASS LINE ===")
            print(json.dumps(verdict, indent=2))
    if args.score or args.quality:
        # CONT-6: score the stream actually sent to OSC (identity slots) next
        # to the tracker's reported set -- one run gives both (output-only).
        import output_quality
        report = output_quality.compare_streams(
            per_frame, scenario, fps=fps,
            max_dancers=int(config.get("max_dancers", 0) or 0) or None)
        print("\n=== OUTPUT STREAMS (tracker vs emitted) ===")
        print(output_quality.format_comparison(report))
        print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
