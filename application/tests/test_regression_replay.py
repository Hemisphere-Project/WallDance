"""Golden regression fixtures (ROADMAP P3 Stage 0 / P4).

Replays recorded sessions through the real pipeline and compares drop/ghost/
swap/track metrics to committed goldens, so a motion-subsystem refactor is
*measurable* rather than judged by eye.  Verified deterministic run-to-run on
the dev box (RTX 3090, fp32).

Opt-in -- it needs GPU + model weights + the recordings and takes ~30s/fixture:

    WD_RUN_REPLAY=1 pytest tests/test_regression_replay.py -v

Workflow across P3:
* Stage 2 (mechanical refactor, single MOG2): metrics must stay within
  tolerance -- proves the rewrite didn't change detection behavior.
* Stage 3 (full collapse of the crossval/bridge trees): metrics WILL move;
  the diff *is* the measured effect.  Re-baseline with ``replay.py --out``
  and commit the new goldens alongside the change, noting the delta.

The fixtures are the corpus-re-founding golden trio (2026-06-10, see
docs/CORPUS_ANALYSIS.md §5): ``hangar-floor`` / ``hangar-aerial`` (the former
``residence1-solo`` slots 3 & 4, same files) + ``texture-aerial`` (aerial on
the heavily textured wall).  Each scenario manifest pins its exact config
snapshot and a recording fingerprint, so the golden is reproducible from the
repo alone -- project renames or config re-saves can no longer drift it.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
GOLDEN_DIR = HERE / "golden"
SCEN_DIR = HERE / "scenarios"
REPLAY = HERE / "replay.py"
REPO = HERE.parent.parent
MODELS_DIR = REPO / "models"
PROJECTS_DIR = REPO / "projects"

GOLDEN_SCENARIOS = ["hangar-floor", "hangar-aerial", "texture-aerial"]

FIXTURES = []
for _name in GOLDEN_SCENARIOS:
    _m = json.loads((SCEN_DIR / f"{_name}.json").read_text())
    FIXTURES.append({"name": _name, "scenario": SCEN_DIR / f"{_name}.json",
                     "project": _m["project"], "slot": _m["slot"],
                     "golden": f"{_name}.json"})

# Per-metric absolute tolerance.  Tight because replay is deterministic; the
# small bands only absorb driver/cuDNN jitter across machines.
TOLERANCE = {
    "real_tracks": 0,
    "ghost_tracks": 1,
    "marginal_tracks": 1,
    "total_tracks": 1,
    "swap_count": 2,
    "zero_detection_frames": 2,
}
AVG_DET_REL_TOL = 0.05  # 5% on average detections/frame


def _recording(project, slot):
    recs = sorted((PROJECTS_DIR / project / "recordings").glob(f"slot_{slot}_*"))
    recs = [r for r in recs if r.suffix in (".avi", ".mp4")]
    return recs[0] if recs else None


def _skip_reasons(fix):
    reasons = []
    if not os.environ.get("WD_RUN_REPLAY"):
        reasons.append("set WD_RUN_REPLAY=1 to run the GPU replay regression")
    golden = GOLDEN_DIR / fix["golden"]
    if not golden.exists():
        reasons.append(f"missing golden {golden.name}")
    else:
        model = json.loads(golden.read_text()).get("model", "yolo11x-pose")
        if not (MODELS_DIR / f"{model}.pt").exists():
            reasons.append(f"missing model weights {model}.pt")
    if _recording(fix["project"], fix["slot"]) is None:
        reasons.append(f"missing recording {fix['project']} slot {fix['slot']}")
    return reasons


@pytest.mark.parametrize("fix", FIXTURES, ids=lambda f: f["name"])
def test_replay_matches_golden(fix):
    reasons = _skip_reasons(fix)
    if reasons:
        pytest.skip("; ".join(reasons))

    golden = json.loads((GOLDEN_DIR / fix["golden"]).read_text())

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "summary.json"
        proc = subprocess.run(
            [sys.executable, str(REPLAY),
             "--scenario", str(fix["scenario"]),
             "--out", str(out)],
            capture_output=True, text=True, timeout=900,
        )
        assert out.exists(), (
            f"replay produced no summary (rc={proc.returncode})\n"
            f"STDERR tail:\n{proc.stderr[-2000:]}")
        got = json.loads(out.read_text())

    # Reproduced the same run?
    assert got["frames_processed"] == golden["frames_processed"]
    assert got["model"] == golden["model"]

    mismatches = []
    for metric, tol in TOLERANCE.items():
        g, x = golden[metric], got[metric]
        if abs(x - g) > tol:
            mismatches.append(f"{metric}: golden={g} got={x} (tol +/-{tol})")
    g_avg, x_avg = golden["avg_detections"], got["avg_detections"]
    if abs(x_avg - g_avg) > AVG_DET_REL_TOL * max(g_avg, 1e-6):
        mismatches.append(
            f"avg_detections: golden={g_avg} got={x_avg} (rel tol {AVG_DET_REL_TOL})")

    assert not mismatches, (
        "replay metrics drifted from golden -- if intended (e.g. P3 Stage 3), "
        "re-baseline with `python tests/replay.py ... --out`:\n  "
        + "\n  ".join(mismatches))
