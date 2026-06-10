"""Unit tests for the Phase D overlay flag logic (TUNING.md Phase D).

Pure logic (status classification + ROI scaling + brighten shape) — no GPU,
caches, or recordings.  Importing ``overlay`` is safe in-process (it imports
detect_cache, which lazy-imports replay → no CUDA-bootstrap re-exec).
"""
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import overlay  # noqa: E402


def _m(n=1, warmup=5, start=1000):
    return {"name": "t", "project": "p", "slot": 0, "start": start,
            "frames": 50, "warmup": warmup, "expected_count": n, "fps": 20.0}


def _rec(frame, reported):
    return {"frame": frame, "abs_frame": 1000 + frame, "reported": reported,
            "ids": list(range(reported)), "tracks": []}


def test_frame_status_warmup_drop_over_ok():
    m = _m(n=1, warmup=5)
    assert overlay._frame_status(_rec(0, 0), m) == "warmup"   # in warmup
    assert overlay._frame_status(_rec(4, 0), m) == "warmup"
    assert overlay._frame_status(_rec(10, 0), m) == "drop"    # under-report
    assert overlay._frame_status(_rec(10, 1), m) == "ok"
    assert overlay._frame_status(_rec(10, 2), m) == "over"    # over-report (ghost)


def test_frame_status_respects_per_range_expected():
    m = _m(warmup=0)
    m["expected_count"] = [{"from": 0, "to": 20, "n": 1}, {"from": 21, "to": 49, "n": 2}]
    assert overlay._frame_status(_rec(10, 2), m) == "over"   # N=1 here
    assert overlay._frame_status(_rec(30, 2), m) == "ok"     # N=2 here
    assert overlay._frame_status(_rec(30, 1), m) == "drop"


def test_resolve_roi_disabled_and_scaled():
    assert overlay._resolve_roi({"roi_enabled": False}, 1000, 1000) is None
    # source dims == frame dims -> identity
    cfg = {"roi_enabled": True, "roi_x": 10, "roi_y": 20, "roi_w": 100, "roi_h": 200,
           "roi_source_w": 1000, "roi_source_h": 1000}
    assert overlay._resolve_roi(cfg, 1000, 1000) == (10, 20, 100, 200)
    # frame is half the source -> coords scale by 0.5
    assert overlay._resolve_roi(cfg, 500, 500) == (5, 10, 50, 100)


def test_brighten_shape_and_dtype():
    # Dark but varied (0..20), like the near-black IR feed with faint structure.
    frame = np.tile(np.linspace(0, 20, 60, dtype=np.uint8), (40, 1))
    frame = np.repeat(frame[:, :, None], 3, axis=2)
    out = overlay._brighten(frame)
    assert out.shape == (40, 60, 3)
    assert out.dtype == np.uint8
    assert out.mean() > frame.mean()  # actually brightened


def test_status_table_covers_all_statuses():
    for s in ("ok", "drop", "over", "warmup"):
        assert s in overlay._STATUS
