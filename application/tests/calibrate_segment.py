#!/usr/bin/env python3
"""calibrate_segment.py — CLI bridge for the unified Calibrate-from-segment flow.

Given ONE recording segment (a slot + window) and the operator-confirmed dancer
count N, this produces a best-effort config the way the in-app Calibrate will:

  1. DETERMINISTIC pass (CPU, path-robust) — re-derive gamma / var / scale /
     person-height / imgsz / confidence-seed / blur from the segment
     (reuses calibrate_project).
  2. CLAHE x confidence PASS-LINE sweep (GPU+TRT) scored vs N — coordinate
     descent: sweep CLAHE (conf at the seed) -> best CLAHE; then sweep confidence
     (CLAHE at best) -> best conf. Uses the FULL pass-line metric (tracker + score
     vs N over the contiguous segment) because a sparse-frame detection proxy
     cannot reproduce the CLAHE verdict (proven 2026-06-16). Reuses sweep_project.
  3. Emits the merged config (deterministic + best CLAHE/conf) + the CLAHE curve.

This is the headless proof of the in-app flow; the DPG Calibrate UI will drive
the same steps (capture/select segment -> confirm N -> run -> apply as seed).

    python tests/calibrate_segment.py --project 6_TANGO_TOGO-night --slot <n> \
        --start 1500 --frames 500 --n 1 --out tmp/togo_night_seed.json
    # or point at an existing scenario's window:
    python tests/calibrate_segment.py --scenario tests/scenarios/outdoor-night.json --n 1
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
import calibrate_project as cp
import sweep_project as sp
import replay

CLAHE_GRID = [1.0, 1.5, 2.5, 4.0, 6.0]
# Pass-line score keys forwarded as the fixed deterministic base for the sweep.
_BASE_KEYS = ("gamma", "mog2_var_threshold", "mog2_scale", "person_height_px",
              "person_height_min_ratio", "person_height_max_ratio",
              "yolo_imgsz", "blur_budget_ms")


def _fingerprint(video: Path) -> dict:
    cap = cv2.VideoCapture(str(video))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 20.0
    cap.release()
    return {"file": video.name, "bytes": video.stat().st_size, "frames": frames, "_fps": fps}


def build_transient_scenario(project, slot, start, frames, n, base_config) -> Path:
    """Write a temp scenario manifest for the segment so replay --scenario can
    score it vs N (expected_count). Class-A pass line (informational only — the
    SCORE scalar drives ranking)."""
    video = replay._find_recording(project, slot)
    if not video:
        raise SystemExit(f"no recording for {project} slot {slot}")
    fp = _fingerprint(Path(video))
    scen = {
        "name": f"segment-{project}-slot{slot}",
        "project": project, "slot": slot, "start": int(start),
        "frames": int(frames), "warmup": 15, "fps": fp.pop("_fps"),
        "expected_count": int(n),
        "pass": {"class": "A", "drop_rate": 0.05, "ghost_rate": 0.05, "longest_drop_s": 1.0},
        "recording_fingerprint": fp,
        "config": base_config,
    }
    tf = Path(tempfile.mkdtemp(prefix="wd_seg_")) / f"{scen['name']}.json"
    tf.write_text(json.dumps(scen, indent=2))
    return tf


def _score(scen_path, base_sets, knobs):
    score, passline = sp._run(str(scen_path), base_sets, knobs)
    return (score["score"] if score else 9.9), passline


def calibrate_segment(scen_path, n, conf_grid=None):
    """Deterministic derivation + coordinate-descent CLAHE/conf pass-line sweep."""
    # 1. Deterministic base (reuses the real calibrators over the segment).
    derived, base = cp.calibrate_project([str(scen_path)])
    conf_seed = derived.get("confidence_seed") or 0.4
    base_sets = [f"{k}={derived[_d(k)]}" for k in _BASE_KEYS if derived.get(_d(k)) is not None]
    base_sets.append("motion_sensitivity=0.55")

    # The sweep scores vs N — rewrite the transient scenario's expected_count.
    scen = json.loads(Path(scen_path).read_text())
    scen["expected_count"] = int(n)
    scen["config"] = base   # pinned base for replay's scenario_config
    Path(scen_path).write_text(json.dumps(scen, indent=2))

    # 2a. CLAHE sweep (conf fixed at seed).
    clahe_scores = {}
    for c in CLAHE_GRID:
        s, _ = _score(scen_path, base_sets, {"clahe_clip": c, "confidence": round(conf_seed, 2)})
        clahe_scores[c] = s
        print(f"  clahe={c} conf={conf_seed:.2f}  score={s}", flush=True)
    best_clahe = min(clahe_scores, key=clahe_scores.get)
    # If EVERY run hit the 9.9 sentinel, every replay subprocess failed to parse
    # (TRT/CUDA broken in the subprocess) — abort loudly rather than emit a
    # garbage seed the operator could Apply (the score curve would be all-9.9).
    if min(clahe_scores.values()) >= 9.9:
        raise RuntimeError("CLAHE sweep produced no valid scores — every replay "
                           "subprocess failed (TRT/CUDA error?); aborting.")

    # 2b. confidence sweep around the seed (CLAHE fixed at best).
    if conf_grid is None:
        conf_grid = sorted({round(max(0.15, min(0.65, conf_seed + d)), 2)
                            for d in (-0.1, 0.0, 0.1)})
    conf_scores = {}
    for cf in conf_grid:
        s, _ = _score(scen_path, base_sets, {"clahe_clip": best_clahe, "confidence": cf})
        conf_scores[cf] = s
        print(f"  clahe={best_clahe} conf={cf}  score={s}", flush=True)
    best_conf = min(conf_scores, key=conf_scores.get)

    # 2c. intermittent-confirm on/off (CLAHE+conf fixed at best). Scene-dependent
    # per G4/bug-#14 (helps aerial/dark/duo by reducing drops, hurts texture/
    # facade), so try both and keep the better. Proven on the duo cases
    # (2026-06-16: texture-duo -0.025, white-duo -0.079). Cheap (2 post-YOLO passes).
    int_scores, int_drops = {}, {}
    for ic in (False, True):
        s, passline = _score(scen_path, base_sets, {
            "clahe_clip": best_clahe, "confidence": best_conf,
            "tracker_intermittent_confirm": str(ic).lower()})
        int_scores[ic] = s
        int_drops[ic] = (passline.get("checks", {}).get("drop_rate", {}).get("value")
                         if passline else None)
        print(f"  clahe={best_clahe} conf={best_conf} intermittent={ic}  score={s}", flush=True)
    best_int = min(int_scores, key=int_scores.get)
    # Dial-B relevance (build #3): if the tuned config still leaves a drop-rate
    # gap-bridging could address (> the class-A drop line), Dial B is worth
    # showing on the live surface; else it's inert -> hidden (raw slider stays
    # in Advanced). gap-bridging (motion_sensitivity) is held at default here.
    best_drop = int_drops.get(best_int)
    dial_b_relevant = bool(best_drop is not None and best_drop > 0.05)

    cfg = dict(base)
    cfg["clahe_clip"] = best_clahe
    cfg["confidence"] = best_conf
    cfg["tracker_intermittent_confirm"] = bool(best_int)
    cfg["dial_b_relevant"] = dial_b_relevant
    for k in _BASE_KEYS:
        if derived.get(_d(k)) is not None:
            cfg[k] = derived[_d(k)]
    return {
        "derived": {k: derived.get(k) for k in
                    ("gamma", "var_threshold", "mog2_scale", "person_height_px",
                     "yolo_imgsz", "confidence_seed", "blur_budget_ms", "ir_limited",
                     "saturation_flags")},
        "clahe_curve": clahe_scores, "best_clahe": best_clahe,
        "conf_curve": conf_scores, "best_conf": best_conf,
        "intermittent_scores": {str(k): v for k, v in int_scores.items()},
        "best_intermittent": bool(best_int),
        "dial_b_relevant": dial_b_relevant,
        "merged_config": cfg,
    }, cfg


def _d(k):
    return {"gamma": "gamma", "mog2_var_threshold": "var_threshold", "mog2_scale": "mog2_scale",
            "person_height_px": "person_height_px",
            "person_height_min_ratio": "person_height_min_ratio",
            "person_height_max_ratio": "person_height_max_ratio",
            "yolo_imgsz": "yolo_imgsz", "blur_budget_ms": "blur_budget_ms"}[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default=None, help="existing scenario JSON (its window)")
    ap.add_argument("--project", default=None)
    ap.add_argument("--slot", type=int, default=None)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--frames", type=int, default=500)
    ap.add_argument("--n", type=int, required=True, help="dancers present in the segment")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    if a.scenario:
        scen_path = Path(a.scenario)
        man = json.loads(scen_path.read_text())
        # copy to temp so we can rewrite expected_count/config without touching the golden
        scen_path = build_transient_scenario(man["project"], man["slot"],
                                             man.get("start", 0), man.get("frames", 500),
                                             a.n, replay.scenario_config(man))
    else:
        if not (a.project and a.slot is not None):
            raise SystemExit("need --scenario OR (--project + --slot)")
        base_cfg = replay._latest_config(a.project) or {}
        scen_path = build_transient_scenario(a.project, a.slot, a.start, a.frames, a.n, base_cfg)

    print(f"calibrate_segment: N={a.n}  segment={scen_path.name}", flush=True)
    result, cfg = calibrate_segment(scen_path, a.n)
    print("\n=== RESULT ===")
    print(json.dumps(result, indent=2))
    if a.out:
        # Write the FULL result (curves + flags + merged_config) so a caller
        # (the in-app Calibrate UI) can display the CLAHE curve + condition flags
        # AND apply the seed config.
        Path(a.out).write_text(json.dumps(result, indent=2))
        print(f"\nwrote sweep result -> {a.out}")


if __name__ == "__main__":
    main()
