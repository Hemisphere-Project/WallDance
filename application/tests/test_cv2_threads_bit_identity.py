"""PERF-1 safety: the motion feed is bit-identical at 1 vs N OpenCV threads.

``import ultralytics`` calls ``cv2.setNumThreads(0)``, so the whole app ran
OpenCV single-threaded. ``core.pipeline`` / ``core.model_manager`` now restore
``CV2_NUM_THREADS`` right after that import (audit 2026-10 PERF-1). That is
only safe if every OpenCV op on the motion path gives the same bytes whatever
the thread count. This test proves it on the full ``MotionModel`` surface the
pipeline and tracker consume:

* ``feed`` state: MOG2 mask, cleaned mask, frame-diff pair + its age, the
  Welford noise accumulators and ``noise_sigma``;
* the queries: ``foreground_blobs`` (global contours), ``foreground_blob`` /
  ``recent_motion_blob`` (per-bbox connected components), ``foreground_ratio``
  and ``recent_motion``.

Two variants:
* real IR footage (``hangar-aerial`` = residence1-solo slot 4, the low-light
  branch), skipped cleanly when the recording is not on this machine;
* synthetic frames (always runs): dark textured wall + noise + a moving
  bright figure, at the show ROI size.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.config import CV2_NUM_THREADS
from core.motion_model import MotionModel

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SCENARIO = HERE / "scenarios" / "hangar-aerial.json"

# The pinned show scales: 0.5 (config default, integer AREA fast path),
# 0.7 (hangar-aerial pin, non-integer AREA) and 0.99 (hangar-floor pin).
SCALES = (0.5, 0.7, 0.99)
N_FRAMES = 14


@pytest.fixture(autouse=True)
def _restore_cv2_threads():
    before = cv2.getNumThreads()
    yield
    cv2.setNumThreads(before)


def _thread_counts():
    """1 vs the app's restored count, and vs every core when that differs."""
    many = {max(2, CV2_NUM_THREADS), max(2, cv2.getNumberOfCPUs())}
    return sorted(many)


def _blob_key(blob):
    if blob is None:
        return None
    return (blob.bbox.tobytes(), blob.centroid.tobytes(), float(blob.area))


def _query_rois(w: int, h: int, blobs):
    """A fixed grid of person-sized boxes plus a box around every global blob."""
    rois = []
    bw, bh = w / 4.0, h / 3.0
    for gy in range(3):
        for gx in range(4):
            rois.append((gx * bw, gy * bh, bw, bh))
    for b in blobs:
        x, y, bw_, bh_ = (float(v) for v in b.bbox)
        rois.append((x - 0.25 * bw_, y - 0.25 * bh_, 1.5 * bw_, 1.5 * bh_))
    return rois


def _run(frames, *, threads: int, scale: float, gamma: float,
         var_threshold: float, person_height: int):
    """Feed every frame; snapshot all outputs after each feed."""
    cv2.setNumThreads(threads)
    assert cv2.getNumThreads() == threads
    mm = MotionModel(scale=scale, var_threshold=var_threshold, fixed_gamma=gamma)
    det = mm.detector
    out = []
    for gray in frames:
        mm.feed(gray)
        blobs = mm.foreground_blobs(person_height, allow_during_warmup=True)
        h, w = gray.shape[:2]
        snap = {
            "fg": det._fg_mask.tobytes(),
            "clean": det._clean_mask.tobytes(),
            "prev": None if det._prev_raw is None else det._prev_raw.tobytes(),
            "curr": None if det._curr_raw is None else det._curr_raw.tobytes(),
            "age": det._diff_pair_age,
            "noise_mean": mm._noise_mean.tobytes(),
            "noise_m2": mm._noise_m2.tobytes(),
            "sigma": mm.noise_sigma(),
            "blobs": [_blob_key(b) for b in blobs],
            "queries": [],
        }
        for roi in _query_rois(w, h, blobs):
            fb, fr = mm.foreground_blob(roi, min_motion_ratio=0.0)
            rb, rr = mm.recent_motion_blob(roi, min_ratio=0.0)
            snap["queries"].append((
                _blob_key(fb), fr, _blob_key(rb), rr,
                mm.foreground_ratio(roi), mm.recent_motion(roi)))
        out.append(snap)
    return out


def _assert_identical(frames, **kw):
    ref = _run(frames, threads=1, **kw)
    assert any(s["blobs"] or any(q[0] for q in s["queries"]) for s in ref), \
        "fixture never produced foreground - the comparison would be vacuous"
    for n in _thread_counts():
        got = _run(frames, threads=n, **kw)
        for i, (a, b) in enumerate(zip(ref, got)):
            for key in a:
                assert a[key] == b[key], (
                    f"frame {i}: '{key}' differs at {n} threads vs 1 "
                    f"(scale={kw['scale']})")


# ---------------------------------------------------------------------------
# Real footage
# ---------------------------------------------------------------------------

def _real_frames():
    scen = json.loads(SCENARIO.read_text())
    cfg = scen["config"]
    rec_dir = REPO / "projects" / scen["project"] / "recordings"
    name = scen.get("recording_fingerprint", {}).get("file")
    path = rec_dir / name if name else None
    if path is None or not path.exists():
        recs = sorted(rec_dir.glob(f"slot_{scen['slot']}_*.avi")) if rec_dir.exists() else []
        path = recs[0] if recs else None
    if path is None or not path.exists():
        pytest.skip(f"recording for {scen['name']} not on this machine")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        pytest.skip(f"cannot open {path}")
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(scen["start"]) + 100)
        x, y, w, h = (int(cfg[k]) for k in ("roi_x", "roi_y", "roi_w", "roi_h"))
        frames = []
        while len(frames) < N_FRAMES:
            ok, bgr = cap.read()
            if not ok:
                break
            # Exactly the replay/file path: ROI crop -> BGR2GRAY.
            frames.append(cv2.cvtColor(bgr[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY))
    finally:
        cap.release()
    if len(frames) < N_FRAMES:
        pytest.skip(f"could only decode {len(frames)} frames from {path.name}")
    return frames, cfg


@pytest.mark.parametrize("scale", SCALES)
def test_motion_feed_bit_identical_real_footage(scale):
    frames, cfg = _real_frames()
    assert float(np.mean(frames[0])) < 55, "expected the low-light branch on this take"
    _assert_identical(frames, scale=scale, gamma=float(cfg.get("gamma", 1.0)),
                      var_threshold=float(cfg.get("mog2_var_threshold", 8)),
                      person_height=int(cfg["person_height_px"]))


# ---------------------------------------------------------------------------
# Synthetic frames (always run)
# ---------------------------------------------------------------------------

def _synthetic_frames(w=1296, h=1195, n=10, base=18):
    rng = np.random.default_rng(1234)
    wall = rng.integers(0, 30, size=(h // 8, w // 8), dtype=np.uint8)
    wall = cv2.resize(wall, (w, h), interpolation=cv2.INTER_CUBIC)
    frames = []
    for i in range(n):
        f = wall.astype(np.int16) + base + rng.integers(-4, 5, size=(h, w))
        # A moving "dancer" (bright ellipse body + head) crossing the wall.
        cx, cy = 300 + 45 * i, 500 + 12 * i
        layer = np.zeros((h, w), np.uint8)
        cv2.ellipse(layer, (cx, cy), (45, 120), 10 * i, 0, 360, 150, -1)
        cv2.circle(layer, (cx, cy - 150), 28, 170, -1)
        f = np.clip(f + layer, 0, 255).astype(np.uint8)
        frames.append(f)
    return frames


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("lowlight", [True, False], ids=["lowlight", "bright"])
def test_motion_feed_bit_identical_synthetic(scale, lowlight):
    frames = _synthetic_frames(base=18 if lowlight else 90)
    _assert_identical(frames, scale=scale, gamma=2.2 if lowlight else 1.0,
                      var_threshold=8.0, person_height=300)


@pytest.mark.parametrize("module", ["core.pipeline", "core.model_manager"])
def test_ultralytics_importers_restore_cv2_threads(module):
    """Importing a module that pulls in ultralytics must leave OpenCV at
    CV2_NUM_THREADS, not ultralytics' 0. Fresh interpreter: the thread count
    is process-global and the module cache would hide the import side effect."""
    pytest.importorskip("ultralytics")
    pytest.importorskip("torch")
    import subprocess
    import sys
    src = str(HERE.parent / "src")
    code = (f"import sys; sys.path.insert(0, {src!r}); import cv2; "
            f"import {module}; import ultralytics; print(cv2.getNumThreads())")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert int(proc.stdout.strip().splitlines()[-1]) == CV2_NUM_THREADS
