#!/usr/bin/env python3
"""calibrate_project.py — headless re-derivation of a project's structural calibration.

Drives the REAL calibrator classes — ``core.calibration.SceneCalibrator`` (Calib1
scene pass) and ``core.calib2.SubjectCollector`` / ``aggregate`` (Calib2 dancer
pass) — over a recording window via the ``replay`` FrameProcessor. No
reimplemented calibration logic: it taps ``proc.process()`` + ``get_last_motion_gray()``
exactly as the live app does, so the derived params match what an in-app
calibration would produce. This re-derives, in coupling order (OPERATOR_V2 §0.1):

    gamma (brightness formula, set BEFORE the window so the var sweep + motion
    gray reflect it) -> mog2 var+scale (FP sweep) -> person_height + ratios +
    imgsz + confidence-seed + blur (detections).

It is the headless equivalent of the in-app "Aim + Calibrate-dancers" flow,
intended for: (a) re-deriving best-effort configs for off/untuned project saves,
(b) feeding the slider sweep (clahe_clip / confidence / motion_sensitivity) a
faithful structural base, and (c) possible future in-app reuse.

Multiple scenarios of ONE project are pooled (a project's slots cohere — they
model the calib-vs-show drift), per the agreed best-effort-baseline procedure:
subject evidence is pooled across slots via ``aggregate``; scene params (gamma /
var / scale) are taken at the across-slot median so no single slot dominates.

Usage:
    # report only
    python tests/calibrate_project.py tests/scenarios/hangar-floor.json \
                                      tests/scenarios/hangar-aerial.json
    # also write a merged config (base + re-derived overrides) for the sweep
    python tests/calibrate_project.py tests/scenarios/hangar-floor.json \
                                      tests/scenarios/hangar-aerial.json \
                                      --out tmp/whitebg2_rederived.json

Notes
-----
* Runs the CPU FrameProcessor path (fp32, YOLO on CUDA) — gamma/var/height are
  path-robust; this matches replay's regression path. The clahe_clip and
  motion_sensitivity *user sliders* are NOT set here — they are swept on top of
  this base on the GPU+TRT show path (the cv2<->kornia divergent knobs).
* ``clahe_clip`` is left at the base value (its real value comes from the sweep,
  not the noise-sigma seed).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np
import cv2

import replay  # noqa: E402  (cuda bootstrap on import)
from core.calibration import SceneCalibrator, seed_gamma, cap_gamma_for_noise
from core.calib2 import SubjectCollector, aggregate
from core.config import AUTOCAL_GAMMA_TARGET, AUTOCAL_GAMMA_BOUNDS

# Smallest YOLO imgsz preset — a derived imgsz here means the dark-scene target
# wants to downscale further than the presets allow (floored).
_IMGSZ_FLOOR = 640


def _gamma_unclamped(brightness: float) -> float:
    """The gamma the brightness->mid-gray formula asks for BEFORE clamping.

    If this sits far above AUTOCAL_GAMMA_BOUNDS[1], the scene is too dark for
    safe software brightening (the clamp is biting) -> an IR/exposure
    (hardware) limit, not a derivation result.
    """
    b = max(1.0, min(250.0, float(brightness)))
    return math.log(b / 255.0) / math.log(AUTOCAL_GAMMA_TARGET / 255.0)


def _track_samples(tracks):
    """(height_px, box_conf|None, speed_px_frame) per confirmed track this frame."""
    out = []
    for t in (tracks or []):
        b = getattr(t, "bbox", None)
        if b is None or len(b) < 4 or b[3] <= 0:
            continue
        bc = getattr(t, "box_conf", None)
        vel = getattr(t, "velocity", None)
        spd = float(np.linalg.norm(vel)) if vel is not None else 0.0
        out.append((float(b[3]), bc, spd))
    return out


def calibrate_window(scenario_path: str, frames: Optional[int] = None) -> dict:
    """Re-derive one slot. Returns scene params + the SubjectRun for pooling."""
    manifest = json.loads(Path(scenario_path).read_text())
    base = replay.scenario_config(manifest)
    video = str(replay._find_recording(manifest["project"], manifest["slot"]))
    model = base.get("model", "yolo11x-pose")
    imgsz = int(base.get("yolo_imgsz", 1280))
    start = int(manifest.get("start", 0))
    nframes = int(frames or manifest.get("frames") or 300)

    proc = replay._build_processor(base, model, imgsz, use_trt=False)
    proc.tracker.reset()

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open video: {video}")
    if start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)

    # Seed gamma from a brightness pre-sample, set BEFORE the window so the var
    # sweep + motion gray see the gamma-enhanced signal (coupling order). ALSO
    # estimate temporal noise from these consecutive frames and cap the gamma the
    # same way the runtime will (cap_gamma_for_noise) — otherwise a noisy
    # near-black scene derives at an over-bright seed (up to the relaxed 4.0
    # bound) that amplifies noise into spurious small detections, corrupting the
    # derived person_height + var (validated 2026-06-16: outdoor-night height
    # 52 at uncapped 4.0 vs ~137 at the capped gamma).
    bvals = []
    nmean = nm2 = None
    nn = 0
    for _ in range(20):
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        bvals.append(float(g.mean()))
        gs = cv2.resize(g, (g.shape[1] // 2, g.shape[0] // 2),
                        interpolation=cv2.INTER_AREA).astype(np.float32)
        nn += 1
        if nmean is None:
            nmean = gs.copy(); nm2 = np.zeros_like(gs)
        else:
            d = gs - nmean; nmean += d / nn; nm2 += d * (gs - nmean)
    brightness = float(np.mean(bvals)) if bvals else 30.0
    noise_pre = (float(np.median(np.sqrt(np.maximum(nm2 / (nn - 1), 0.0))))
                 if nn >= 2 else 0.0)
    gamma, _ = cap_gamma_for_noise(seed_gamma(brightness), noise_pre)
    proc.enhancer.gamma = gamma
    if hasattr(proc.enhancer, "_update_gamma_lut"):
        proc.enhancer._update_gamma_lut()
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)

    roi = tuple(int(base.get(k) or 0) for k in ("roi_x", "roi_y", "roi_w", "roi_h"))
    roi_src = (int(base.get("roi_source_w") or 0), int(base.get("roi_source_h") or 0))

    scene = SceneCalibrator(window_frames=nframes)
    subj = SubjectCollector(window_frames=nframes)
    scene.start()
    subj.start("replay", base.get("profile", "show"), roi, roi_src, imgsz)

    n = 0
    while n < nframes:
        ok, fr = cap.read()
        if not ok:
            break
        tracks, _e, timing, _l = proc.process(fr, need_preview=False, frame_number=n)
        gray = proc.get_last_motion_gray() if hasattr(proc, "get_last_motion_gray") else None
        samples = _track_samples(tracks)
        fps_s = (1000.0 / timing["total"]) if timing.get("total") else 20.0
        scene.feed(gray, [s[0] for s in samples], fps_s, 0.0,
                   brightness=float(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).mean()))
        subj.feed(samples, fps_s)
        n += 1
    cap.release()

    res = scene.compute()
    run = subj.finish()
    capped, was_capped = cap_gamma_for_noise(gamma, res.noise_sigma)
    g_want = _gamma_unclamped(brightness)
    return {
        "scenario": manifest["name"],
        "project": manifest["project"],
        "frames": n,
        "brightness_mean": round(brightness, 2),
        "noise_sigma": round(res.noise_sigma, 3),
        "gamma": float(capped if was_capped else gamma),
        "gamma_capped": was_capped,
        "gamma_unclamped": round(g_want, 2),
        "gamma_clamped_hi": bool(g_want > AUTOCAL_GAMMA_BOUNDS[1] + 1e-6),
        "var_threshold": float(res.var_threshold) if res.var_ok else None,
        "mog2_scale": float(res.mog2_scale) if res.var_ok else None,
        "var_saturated": bool(res.var_saturated),
        "scene_height_px": res.person_height_px if res.height_ok else None,
        "_run": run,
        "_roi": roi,
        "_roi_src": roi_src,
        "_base": base,
    }


def calibrate_project(scenario_paths: List[str], frames: Optional[int] = None) -> dict:
    """Re-derive across a project's slots: pool subject evidence, median scene params."""
    slots = [calibrate_window(p, frames) for p in scenario_paths]
    runs = [s["_run"] for s in slots]
    roi = slots[0]["_roi"]
    roi_src = slots[0]["_roi_src"]
    roi_long = max(roi[2], roi[3]) or max(roi_src) or 1280
    noise = float(np.median([s["noise_sigma"] for s in slots]))
    prop = aggregate(runs, roi_long, noise_sigma=noise)

    # Scene params at the across-slot median (var/scale kept paired via the
    # median-var slot, so the chosen pair stays internally consistent).
    gamma = float(np.median([s["gamma"] for s in slots]))
    var_slots = [s for s in slots if s["var_threshold"] is not None]
    if var_slots:
        var_slots.sort(key=lambda s: s["var_threshold"])
        mid = var_slots[len(var_slots) // 2]
        var_t, scale = mid["var_threshold"], mid["mog2_scale"]
    else:
        var_t = scale = None

    # Saturation advisory: a derived value pinned to its bound is the algorithm
    # hitting a wall, not calibrating. Surface it so a capped project is never
    # mistaken for a solved one (operator concern 2026-06-16).
    imgsz = prop.imgsz if prop.ok else None
    gamma_clamped = any(s["gamma_clamped_hi"] for s in slots)
    g_want_max = max(s["gamma_unclamped"] for s in slots)
    imgsz_floored = bool(imgsz == _IMGSZ_FLOOR)
    var_sat = any(s["var_saturated"] for s in slots)
    flags = []
    if gamma_clamped:
        flags.append(f"gamma clamped at {AUTOCAL_GAMMA_BOUNDS[1]} "
                     f"(formula wanted {g_want_max:.1f}) -> IR/exposure-limited, "
                     "fix at the sensor not in software")
    if imgsz_floored:
        flags.append(f"imgsz floored at {_IMGSZ_FLOOR} (dark-scene target wants "
                     "further downscale than presets allow)")
    if var_sat:
        flags.append("mog2 var saturated (no candidate met the FP target -> "
                     "noisy background)")
    ir_limited = gamma_clamped  # the brightness ceiling is the load-bearing one

    derived = {
        "project": slots[0]["project"],
        "scenarios": [s["scenario"] for s in slots],
        "per_slot": [{k: v for k, v in s.items() if not k.startswith("_")}
                     for s in slots],
        "gamma": round(gamma, 4),
        "var_threshold": var_t,
        "mog2_scale": scale,
        "noise_sigma": round(noise, 3),
        "person_height_px": prop.person_height_px if prop.ok else None,
        "person_height_min_ratio": prop.min_ratio if prop.ok else None,
        "person_height_max_ratio": prop.max_ratio if prop.ok else None,
        "yolo_imgsz": imgsz,
        "confidence_seed": prop.confidence,
        "blur_budget_ms": prop.blur_budget_ms,
        "subj_samples": prop.samples,
        "ir_limited": ir_limited,
        "saturation_flags": flags,
    }
    return derived, slots[0]["_base"]


def merged_config(base: dict, derived: dict) -> dict:
    """base config + re-derived structural overrides (clahe/motion left to sweep)."""
    cfg = dict(base)
    cfg["gamma"] = derived["gamma"]
    if derived["var_threshold"] is not None:
        cfg["mog2_var_threshold"] = derived["var_threshold"]
        cfg["mog2_scale"] = derived["mog2_scale"]
    if derived["person_height_px"]:
        cfg["person_height_px"] = derived["person_height_px"]
        cfg["person_height_min_ratio"] = derived["person_height_min_ratio"]
        cfg["person_height_max_ratio"] = derived["person_height_max_ratio"]
    if derived["yolo_imgsz"]:
        cfg["yolo_imgsz"] = derived["yolo_imgsz"]
    if derived["confidence_seed"] is not None:
        cfg["confidence"] = derived["confidence_seed"]
    if derived["blur_budget_ms"] is not None:
        cfg["blur_budget_ms"] = derived["blur_budget_ms"]
    return cfg


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="+", help="scenario JSON(s) of ONE project")
    ap.add_argument("--frames", type=int, default=None,
                    help="window length per slot (default: scenario's frames)")
    ap.add_argument("--out", default=None,
                    help="write the merged config (base + re-derived) to this path")
    a = ap.parse_args()
    derived, base = calibrate_project(a.scenarios, a.frames)
    print(json.dumps(derived, indent=2))
    if a.out:
        cfg = merged_config(base, derived)
        Path(a.out).write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"\nwrote merged config -> {a.out}")


if __name__ == "__main__":
    main()
