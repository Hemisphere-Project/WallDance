"""Detect-pass cache equivalence (TUNING.md Phase B).

The cache is only useful if replaying *from* it is identical to a live replay.
This opt-in GPU test builds a small cache and asserts the cache replay matches a
full replay (same window, same config) frame-for-frame and metric-for-metric.

    WD_RUN_REPLAY=1 pytest tests/test_detect_cache.py -v

Like the golden regression it needs GPU + model weights + the recordings, so it
skips by default.  Pure-Python cache *key/path* logic is tested unconditionally.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPLAY = HERE / "replay.py"
REPO = HERE.parent.parent
MODELS_DIR = REPO / "models"
PROJECTS_DIR = REPO / "projects"

# residence1-solo was renamed 3_TANGO_HANGAR-whitebg2 in the 2026-06-10 corpus
# re-founding (same recording); slot 4 is the hangar-aerial scenario.
PROJECT = "3_TANGO_HANGAR-whitebg2"
SLOT = 4
SCENARIO = HERE / "scenarios" / "hangar-aerial.json"
# A short window that still spans a real drop region (abs 1643-1654) so the
# equivalence is exercised on non-trivial gate/bridge behaviour, not just empty
# frames.
START = 1600
FRAMES = 120


def _recording(project, slot):
    recs = sorted((PROJECTS_DIR / project / "recordings").glob(f"slot_{slot}_*"))
    recs = [r for r in recs if r.suffix in (".avi", ".mp4")]
    return recs[0] if recs else None


def _skip_reasons():
    reasons = []
    if not os.environ.get("WD_RUN_REPLAY"):
        reasons.append("set WD_RUN_REPLAY=1 to run the GPU cache-equivalence test")
    if _recording(PROJECT, SLOT) is None:
        reasons.append(f"missing recording {PROJECT} slot {SLOT}")
    if not (MODELS_DIR / "yolo11x-pose_1280.engine").exists():
        reasons.append("missing TRT engine yolo11x-pose_1280.engine")
    return reasons


# --------------------------------------------------------------------------- #
# Pure-Python cache key/path logic (always runs)
# --------------------------------------------------------------------------- #
def test_cache_key_sensitive_to_rebuild_params():
    import detect_cache
    base = {"confidence": 0.37, "yolo_imgsz": 1280, "gamma": 1.4, "roi_x": 10}
    k1 = detect_cache.cache_key(base, "v.avi", 0, 100, "m", 1280)
    # changing a YOLO-front-end param -> different cache
    k2 = detect_cache.cache_key({**base, "confidence": 0.5}, "v.avi", 0, 100, "m", 1280)
    assert detect_cache._key_hash(k1) != detect_cache._key_hash(k2)
    # changing a tunable (post-YOLO) param -> SAME cache (not in the key)
    k3 = detect_cache.cache_key({**base, "mog2_var_threshold": 99}, "v.avi", 0, 100, "m", 1280)
    assert detect_cache._key_hash(k1) == detect_cache._key_hash(k3)
    # window change -> different cache
    k4 = detect_cache.cache_key(base, "v.avi", 50, 100, "m", 1280)
    assert detect_cache._key_hash(k1) != detect_cache._key_hash(k4)


# --------------------------------------------------------------------------- #
# Cache replay == full replay (opt-in GPU)
# --------------------------------------------------------------------------- #
def _run_replay(td, name, extra):
    """Drive replay.py as a subprocess (its CUDA bootstrap re-execs, which would
    wreck an in-process pytest session -- the golden test uses the same trick).
    Returns (summary, timeline)."""
    out = Path(td) / f"{name}_sum.json"
    tl = Path(td) / f"{name}_tl.json"
    proc = subprocess.run(
        [sys.executable, str(REPLAY), "--scenario", str(SCENARIO),
         "--start", str(START), "--frames", str(FRAMES),
         "--out", str(out), "--timeline", str(tl)] + extra,
        capture_output=True, text=True, timeout=900)
    assert out.exists() and tl.exists(), (
        f"replay {name} produced no output (rc={proc.returncode})\n"
        f"STDERR tail:\n{proc.stderr[-2000:]}")
    return json.loads(out.read_text()), json.loads(tl.read_text())


def test_cache_replay_matches_full_replay():
    reasons = _skip_reasons()
    if reasons:
        pytest.skip("; ".join(reasons))

    # Track P: both run the GPU+TRT show path (byte-stable run-to-run); --cache
    # must reproduce the full --trt run frame-for-frame.
    with tempfile.TemporaryDirectory() as td:
        full_sum, full_tl = _run_replay(td, "full", ["--trt"])
        cache_sum, cache_tl = _run_replay(td, "cache", ["--cache", "--trt"])

    # Frame-for-frame identical reported timeline.
    assert cache_tl == full_tl

    # And every summary metric identical (not just within tolerance).
    for k in ("frames_processed", "real_tracks", "marginal_tracks",
              "ghost_tracks", "total_tracks", "swap_count", "gate_rejections",
              "dormant_count", "resurrect_count", "zero_detection_frames",
              "avg_detections"):
        assert cache_sum[k] == full_sum[k], (k, full_sum[k], cache_sum[k])
