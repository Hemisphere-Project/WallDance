"""Smoke test for the soak harness (TODO Phase 7).

Gated like the replay regression tests: needs the corpus recording + YOLO
weights on disk, so it only runs with WD_RUN_REPLAY=1.
"""
import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("WD_RUN_REPLAY") != "1",
    reason="soak smoke needs recordings + model weights (set WD_RUN_REPLAY=1)",
)

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def test_soak_smoke(tmp_path):
    import soak

    rc = soak.main([
        "--scenario", "hangar-floor",
        "--max-chunks", "1",
        "--chunk-frames", "20",
        "--start", "1500",
        "--frames", "300",
        "--out", str(tmp_path),
    ])
    assert rc == 0

    progress = (tmp_path / "progress.jsonl").read_text().strip().splitlines()
    assert len(progress) == 1
    rec = json.loads(progress[0])
    assert rec["frames"] == 20
    assert rec["rss_mb"] > 0
    assert (tmp_path / "SUMMARY.md").read_text().startswith("# Soak run")
